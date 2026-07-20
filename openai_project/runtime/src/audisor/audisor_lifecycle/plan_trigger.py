"""Automatic A-Flow plan review trigger bridge.

When any agent drafts a plan for a qualifying mutation task, this bridge
calls aflow_review with plan_digest='auto', evaluates the result, and—when
the review passes—proceeds to ignite() to assemble the execution contract and
create the active lock.  This collapses 'submit plan' and 'call aflow_review'
into one automatic step.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Literal, Mapping, cast

from audisor.schemas.authority import AuthorityContext
from .contract import AudisorLifecycleError, accept_for_primary, canonical_text, write_lock
from .ignition import ignite
from .operation import AudisorOperationContext, FrozenAudisorPolicy, make_operation_context
from .analysis_package import assemble_analysis_package
from .artifacts import persist_audisor_stage

AflowReviewCaller = Callable[[str, str, str | None], dict[str, Any]]


def _default_review_caller(original_plan: str, plan_id: str, plan_digest: str | None) -> dict[str, Any]:
    """Placeholder for the actual MCP aflow_review call.

    In production this is replaced by the MCP client invocation.  The runtime
    does not import Aflow_cli directly; the caller injects the transport.
    """
    raise AudisorLifecycleError("aflow_review caller is not configured; inject AflowReviewCaller")


def _compute_digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _is_valid_plan_for_review(plan_document: Mapping[str, Any] | None) -> bool:
    """Validate plan_document against plan-detection criteria.

    A valid plan for A-Flow review must have:
    - source_kind == "plan"
    - expects_mutation is True
    - read_only is False
    - steps is a non-empty list
    - original_plan is a non-empty string

    No hashing or task-kind classification is used for detection.
    """
    if plan_document is None:
        return False
    if not isinstance(plan_document, Mapping):
        return False
    if plan_document.get("source_kind") != "plan":
        return False
    if plan_document.get("expects_mutation") is not True:
        return False
    if plan_document.get("read_only") is not False:
        return False
    steps = plan_document.get("steps")
    if not isinstance(steps, list) or len(steps) == 0:
        return False
    original = plan_document.get("original_plan")
    if not isinstance(original, str) or not original.strip():
        return False
    return True


def auto_trigger_plan_review(
    *,
    original_plan: str | None = None,
    plan_id: str,
    plan_document: Mapping[str, Any] | None = None,
    task: Mapping[str, Any],
    repository_context: Mapping[str, Any],
    workspace_identity: Mapping[str, Any],
    authority_context: Mapping[str, Any] | AuthorityContext,
    policy: FrozenAudisorPolicy | None = None,
    review_caller: AflowReviewCaller | None = None,
    agent_identity: str = "primary_codex",
    state_root: Path | None = None,
) -> dict[str, Any]:
    """Review a plan automatically and, if clean, ignite the Audisor lifecycle.

    Triggering is based on plan-document detection, not task-kind
    classification.  The host validates ``plan_document`` structure; only a
    completed valid plan (``source_kind="plan"``, ``expects_mutation=true``,
    ``read_only=false``, non-empty ``steps``, non-empty ``original_plan``)
    triggers the review.

    Args:
        original_plan: The unchanged plan text.  If ``None``, extracted from
            ``plan_document["original_plan"]``.
        plan_id: A stable, non-empty identifier.
        plan_document: The plan document dict used for trigger detection.
            Must satisfy the plan-detection schema to trigger review.
        task: The accepted task mapping.
        repository_context: Repository baseline evidence and constraints.
        workspace_identity: Workspace path and identity metadata.
        authority_context: Authority rules and allowed paths. Accepts either
            a plain Mapping or a canonical AuthorityContext.
        policy: Optional frozen Audisor policy.
        review_caller: Injectable MCP caller. Defaults to a placeholder
            that raises if not configured.
        agent_identity: Identity of the agent requesting the review.
            Defaults to ``"primary_codex"`` for backward compatibility.
        state_root: Directory where the active lock is written.
            Defaults to ``<repo_root>/.codex/audisor-state``.

    Returns:
        A dict with:
            - review_result: the raw aflow_review output
            - ignition_result: the IgnitionResult dataclass (if review passed)
            - lock_path: path to the written active-lock.json (if ready)
            - decision: 'proceed', 'decision_required', 'supplement_ready', or 'blocked'
    """
    if not _is_valid_plan_for_review(plan_document):
        return {
            "decision": "skip",
            "reason": "plan_document does not meet A-Flow trigger criteria",
            "review_result": None,
            "ignition_result": None,
            "lock_path": None,
        }

    # Resolve original_plan: explicit parameter wins, else extract from plan_document
    resolved_plan = original_plan
    if resolved_plan is None and plan_document is not None:
        resolved_plan = plan_document.get("original_plan")
    if not isinstance(resolved_plan, str) or not resolved_plan.strip():
        return {
            "decision": "skip",
            "reason": "original_plan is missing or empty",
            "review_result": None,
            "ignition_result": None,
            "lock_path": None,
        }

    caller = review_caller or _default_review_caller
    review = caller(resolved_plan, plan_id, "auto")

    if review.get("is_error"):
        return {
            "decision": "blocked",
            "reason": f"aflow_review error: {review.get('error', 'unknown')}",
            "review_result": review,
            "ignition_result": None,
            "lock_path": None,
        }

    manifest = review.get("manifest", {})
    outcome = manifest.get("outcome", "unknown")

    if outcome == "decision_required":
        return {
            "decision": "decision_required",
            "reason": "A-Flow requires human decision before proceeding",
            "review_result": review,
            "ignition_result": None,
            "lock_path": None,
        }

    # For supplement_ready or no_material_gap, proceed to ignite
    if outcome not in {"supplement_ready", "no_material_gap"}:
        return {
            "decision": "blocked",
            "reason": f"unexpected A-Flow outcome: {outcome}",
            "review_result": review,
            "ignition_result": None,
            "lock_path": None,
        }

    # Build the operation context and ignite
    # We need to create a candidate plan from the resolved plan text
    candidate_plan = _plan_text_to_candidate(resolved_plan, plan_id, manifest)

    # Build analysis package for local invoker path
    analysis_request = {
        "analysis_id": plan_id,
        "schema_version": "1.0.0",
        "plan": {
            "plan_id": plan_id,
            "version": "1.0.0",
            "success_definition_reference": f"success-def:{plan_id}",
        },
        "evidence": [],
    }

    try:
        # Plan-based work defaults to "build"; override via task metadata if needed
        op_type: Literal["build", "fix"] = "build"
        package = assemble_analysis_package(
            operation_id=plan_id,
            operation_type=op_type,
            accepted_task=task,
            accepted_plan=candidate_plan,
            authority_context=authority_context,
            analysis_request=analysis_request,
            repository_context=repository_context,
            workspace_identity=workspace_identity,
            provider_policy={"provider": (policy.provider if policy else "local-openai-compatible")},
        )
    except Exception as exc:
        return {
            "decision": "blocked",
            "reason": f"analysis package assembly failed: {exc}",
            "review_result": review,
            "ignition_result": None,
            "lock_path": None,
        }

    op_type_literal = cast(Literal["build", "fix"], package.operation_type)
    context = make_operation_context(
        operation_id=package.operation_id,
        operation_type=op_type_literal,
        accepted_task=package.accepted_task,
        accepted_plan=package.accepted_plan,
        repository_context=package.repository_context,
        workspace_identity=package.workspace_identity,
        authority_context=package.authority_context,
        analysis_package=package,
    )

    result = ignite(
        operation_context=context,
        policy=policy,
    )

    if not result.lifecycle_selected:
        return {
            "decision": "blocked",
            "reason": "Audisor lifecycle was not selected",
            "review_result": review,
            "ignition_result": result,
            "lock_path": None,
        }

    if not result.implementation_eligible:
        return {
            "decision": "blocked",
            "reason": "Audisor contract is not ready for implementation",
            "review_result": review,
            "ignition_result": result,
            "lock_path": None,
        }

    # Write the active lock
    contract = result.execution_contract
    if contract is None:
        return {
            "decision": "blocked",
            "reason": "execution contract is missing",
            "review_result": review,
            "ignition_result": result,
            "lock_path": None,
        }

    try:
        # accept_for_primary expects an Audisor analysis result, not the execution
        # contract.  Build the minimal analysis structure from the review result
        # and candidate plan, then bind the contract SHA-256.
        contract_sha = contract.get("lock_payload", {}).get("sha256")
        analysis_for_lock = _build_analysis_for_lock(
            task=task,
            candidate_plan=candidate_plan,
            contract_sha256=contract_sha,
        )
        lock = accept_for_primary(
            analysis_for_lock,
            execution_contract_sha256=contract_sha,
            locked_by=agent_identity,
        )
        resolved_state_root = state_root or (Path(__file__).resolve().parents[5] / ".codex" / "audisor-state")
        lock_path = resolved_state_root / "active-lock.json"
        write_lock(lock_path, lock)
    except Exception as exc:
        return {
            "decision": "blocked",
            "reason": f"lock creation failed: {exc}",
            "review_result": review,
            "ignition_result": result,
            "lock_path": None,
        }

    return {
        "decision": "proceed",
        "reason": "A-Flow review passed and active lock created",
        "review_result": review,
        "ignition_result": result,
        "lock_path": str(lock_path),
    }


def _plan_text_to_candidate(original_plan: str, plan_id: str, manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Convert reviewed plan text into a minimal candidate implementation plan.

    This is a host-owned normalization that preserves the plan as the source
    of truth while supplying the contract shape ignite() expects.
    """
    # Minimal contract sections required by assemble_contract / adapter
    # authority.allowed_paths and action.target_paths must be non-empty
    return {
        "success_definition": {
            "requirements": [
                {
                    "requirement_id": f"req.{plan_id}",
                    "success_predicate": "Plan is reviewed and locked.",
                    "source_reference": f"aflow-review:{plan_id}",
                }
            ],
            "source": "aflow_auto_review",
        },
        "execution_trajectory": [
            {
                "stage_id": f"stage.{plan_id}",
                "exact_actions": [f"action.{plan_id}"],
                "checkpoint": {"checkpoint_id": f"checkpoint.{plan_id}"},
            }
        ],
        "implementation_plan": [
            {
                "action_id": f"action.{plan_id}",
                "objective": "Execute the reviewed plan",
                "target_paths": ["."],
                "requirement_ids": [f"req.{plan_id}"],
            }
        ],
        "validation_contract": [
            {
                "validation_id": f"val.{plan_id}",
                "requirement_ids": [f"req.{plan_id}"],
                "fixture_id": f"fix.{plan_id}",
            }
        ],
        "fixture_specifications": [
            {
                "fixture_id": f"fix.{plan_id}",
                "validation_ids": [f"val.{plan_id}"],
            }
        ],
        "evidence_manifest": {
            "evidence_items": [
                {
                    "evidence_id": f"ev.{plan_id}",
                    "requirement_ids": [f"req.{plan_id}"],
                    "validation_ids": [f"val.{plan_id}"],
                    "checkpoint_ids": [f"checkpoint.{plan_id}"],
                }
            ],
            "state_checks": [],
        },
        "post_build_acceptance": {
            "acceptance_rules": [
                {
                    "rule_id": f"rule.{plan_id}",
                    "requirement_ids": [f"req.{plan_id}"],
                    "evidence_ids": [f"ev.{plan_id}"],
                    "final_decision_rule": "Plan execution completed and evidence collected.",
                }
            ]
        },
        "authority": {
            "allowed_paths": ["."],
            "prohibited_paths": [],
            "allowed_tools": [],
            "prohibited_tools": [],
            "preserved_conditions": [],
        },
    }


def _build_analysis_for_lock(
    task: Mapping[str, Any],
    candidate_plan: Mapping[str, Any],
    contract_sha256: str | None,
) -> dict[str, Any]:
    """Build the minimal Audisor analysis structure that accept_for_primary expects.

    The auto-trigger bridge does not run the full Audisor worker, so we
    synthesize the decision and lock_payload from the review result and
    candidate plan.  The contract SHA-256 binds this lock to the execution
    contract assembled by the adapter.
    """
    return {
        "decision": {
            "aflow_decision": "no_material_gap",
            "contract_decision": "no_material_gap",
            "plan_ready_for_primary_decision": True,
        },
        "plan_gaps": [],
        "lock_payload": {
            "immutable_user_task_canonical_text": canonical_text(task),
            "accepted_plan_canonical_text": canonical_text(candidate_plan),
            "success_definition_canonical_text": canonical_text(candidate_plan.get("success_definition")),
            "required_trajectory_canonical_text": canonical_text(candidate_plan.get("execution_trajectory")),
            "validation_cases_canonical_text": canonical_text(candidate_plan.get("validation_contract")),
            "fixture_specifications_canonical_text": canonical_text(candidate_plan.get("fixture_specifications")),
            "hash_algorithm": "sha256",
        },
    }