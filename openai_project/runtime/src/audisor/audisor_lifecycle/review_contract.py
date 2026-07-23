"""Public lifecycle wrapper: review → lock → state.

This module is the single public entry point for the A-Flow MCP review path.
It accepts a complete schema-v1 ``analysis_request`` and explicit contract
assembly inputs, calls the real ``aflow.analyze()``, maps the decision into
the existing contract adapter, creates the primary lock, and writes the
complete hook-compatible active-state envelope.

Operation status, analysis decisions, and execution contracts are persisted
through the existing ``AudisorOperationStore`` and ``ArtifactStore`` so a
later ``aflow_artifacts`` tool can wrap them.

No raw plan-text normalization is performed here.  The caller must supply
fully structured inputs.  Raw-text normalization is a deferred host-adapter
capability.

No name beginning with ``_`` is imported from A-Flow or the runtime lifecycle.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from audisor.operations.artifacts import ArtifactStore
from audisor.operations.store import AudisorOperationStore

from .active_state import write_active_state
from .adapter import assemble_contract
from .contract import AudisorLifecycleError, accept_for_primary, canonical_text


def map_decision_to_frozen(decision: Mapping[str, Any]) -> dict[str, Any]:
    """Map an ``aflow.analyze()`` decision to the frozen_aflow_result format.

    The contract adapter expects::

        {"decision": "<enum>", "unresolved_items": [...]}

    The A-Flow decision contains ``findings`` which become ``unresolved_items``
    when the decision is not clean.
    """
    aflow_decision = decision.get("decision", "material_gap_found")
    findings = decision.get("findings", [])
    unresolved = list(findings) if aflow_decision != "no_material_gap" else []
    return {
        "decision": aflow_decision,
        "unresolved_items": unresolved,
    }


def build_analysis_for_lock(
    candidate_plan: Mapping[str, Any],
    accepted_task_input: Mapping[str, Any],
    execution_contract_sha256: str,
) -> dict[str, Any]:
    """Build the analysis structure that ``accept_for_primary`` expects.

    The lock payload canonical text fields are derived from the candidate
    implementation plan sections.  This is the single public transformation
    that both the MCP path and the plan-trigger path must use.
    """
    return {
        "decision": {
            "aflow_decision": "no_material_gap",
            "contract_decision": "no_material_gap",
            "plan_ready_for_primary_decision": True,
        },
        "plan_gaps": [],
        "lock_payload": {
            "immutable_user_task_canonical_text": canonical_text(accepted_task_input),
            "accepted_plan_canonical_text": canonical_text(candidate_plan),
            "success_definition_canonical_text": canonical_text(
                candidate_plan.get("success_definition")
            ),
            "required_trajectory_canonical_text": canonical_text(
                candidate_plan.get("execution_trajectory")
            ),
            "validation_cases_canonical_text": canonical_text(
                candidate_plan.get("validation_contract")
            ),
            "fixture_specifications_canonical_text": canonical_text(
                candidate_plan.get("fixture_specifications")
            ),
            "hash_algorithm": "sha256",
        },
    }


def review_and_lock(
    *,
    analysis_request: Mapping[str, Any],
    accepted_task_input: Mapping[str, Any],
    candidate_implementation_plan: Mapping[str, Any],
    authority: Mapping[str, Any],
    baseline_evidence: Any,
    accepted_constraints: Any,
    required_outputs: Any,
    operation_id: str,
    state_root: Path,
    locked_by: str = "aflow-mcp",
    operation_store: AudisorOperationStore | None = None,
    artifact_store: ArtifactStore | None = None,
) -> dict[str, Any]:
    """Execute the full review → lock → state pipeline.

    Args:
        analysis_request: Complete schema-v1 analysis request (8 fields).
        accepted_task_input: Task input for contract assembly.
        candidate_implementation_plan: Plan with all 7 PLAN_SECTIONS.
        authority: Authority mapping (allowed_paths, prohibited_paths, etc.).
        baseline_evidence: Baseline evidence for contract assembly.
        accepted_constraints: Constraints for contract assembly.
        required_outputs: Required outputs for contract assembly.
        operation_id: Caller-supplied operation identifier.
        state_root: Directory for the active-state envelope.
        locked_by: Agent identity for the lock.
        operation_store: Optional operation store for idempotency and
            persistence.  Defaults to ``state_root / "operations"``.
        artifact_store: Optional artifact store for contract and lock
            persistence.  Defaults to ``state_root / "artifacts"``.

    Returns:
        A structured result dict with:
            - status: "ok" or "blocked"
            - decision: the A-Flow decision string
            - blocking: bool
            - execution_ready: bool
            - findings: list of findings
            - lock_state: {"present": bool, "valid": bool, ...}
            - contract_sha256: str | None
            - state_path: str | None
            - operation_id: str
            - operation_status: str (from store)
            - artifacts: list of artifact references (from store)

    Raises:
        AudisorLifecycleError: On state conflicts or verification failures.
    """
    from aflow import analyze

    # Resolve stores
    if operation_store is None:
        operation_store = AudisorOperationStore(state_root / "operations")
    if artifact_store is None:
        artifact_store = ArtifactStore(state_root / "artifacts")

    # 0. Idempotency and conflict check via operation store
    request_payload = {
        "analysis_request": dict(analysis_request),
        "accepted_task_input": dict(accepted_task_input),
        "candidate_implementation_plan": dict(candidate_implementation_plan),
        "authority": dict(authority),
        "operation_id": operation_id,
    }
    create_status, existing_state = operation_store.create(operation_id, request_payload)

    if create_status == "conflict":
        raise AudisorLifecycleError(
            f"Operation '{operation_id}' is already bound to a different request"
        )
    if create_status == "existing" and existing_state is not None:
        # Idempotent replay: return cached outcome
        cached: dict[str, Any] = {
            "status": "ok" if existing_state.status == "completed" else "blocked",
            "decision": "no_material_gap" if existing_state.status == "completed" else "material_gap_found",
            "blocking": existing_state.status != "completed",
            "execution_ready": existing_state.status == "completed",
            "findings": [],
            "rejected_findings": [],
            "lock_state": {"present": existing_state.status == "completed", "valid": existing_state.status == "completed"},
            "contract_sha256": existing_state.result_hash,
            "state_path": None,
            "operation_id": operation_id,
            "operation_status": existing_state.status,
            "artifacts": [dict(a) for a in existing_state.artifacts],
            "idempotent_replay": True,
        }
        return cached

    # Mark operation as running
    operation_store.start(operation_id)

    # 1. Call the real deterministic analysis
    decision = analyze(dict(analysis_request))

    aflow_decision = decision.get("decision", "material_gap_found")
    blocking = decision.get("blocking", True)
    execution_ready = decision.get("execution_ready", False)
    findings = decision.get("findings", [])

    result: dict[str, Any] = {
        "status": "ok",
        "decision": aflow_decision,
        "blocking": blocking,
        "execution_ready": execution_ready,
        "findings": findings,
        "rejected_findings": decision.get("rejected_findings", []),
        "lock_state": {"present": False, "valid": False},
        "contract_sha256": None,
        "state_path": None,
        "operation_id": operation_id,
        "operation_status": "running",
        "artifacts": [],
    }

    # 2. If blocking, persist the blocking result and return without creating state
    if blocking or aflow_decision != "no_material_gap":
        result["status"] = "blocked"
        # Persist blocking outcome through the operation store
        block_reason = f"aflow_decision={aflow_decision}; findings={len(findings)}"
        operation_store.block(operation_id, block_reason)
        # Persist the analysis decision as an artifact
        decision_ref = artifact_store.persist(
            operation_id,
            "analysis-decision",
            json.dumps(decision, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            artifact_type="analysis",
        )
        result["operation_status"] = "blocked"
        result["artifacts"] = [decision_ref.to_mapping()]
        return result

    # 3. Map decision to frozen format and assemble contract
    frozen = map_decision_to_frozen(decision)
    assembly_input = {
        "frozen_aflow_result": frozen,
        "accepted_task_input": dict(accepted_task_input),
        "candidate_implementation_plan": dict(candidate_implementation_plan),
        "authority": dict(authority),
        "baseline_evidence": baseline_evidence,
        "accepted_constraints": accepted_constraints,
        "required_outputs": required_outputs,
    }
    contract = assemble_contract(assembly_input)["aflow_execution_contract"]
    contract_sha = contract["lock_payload"]["sha256"]

    # 4. Build analysis for lock and create the primary lock
    analysis_for_lock = build_analysis_for_lock(
        candidate_implementation_plan,
        accepted_task_input,
        contract_sha,
    )
    lock = accept_for_primary(
        analysis_for_lock,
        execution_contract_sha256=contract_sha,
        locked_by=locked_by,
    )

    # 5. Write the complete active-state envelope
    state_path = write_active_state(
        state_root,
        operation_id=operation_id,
        primary_lock=lock,
        execution_contract=contract,
    )

    # 6. Persist contract and lock through the artifact store
    contract_ref = artifact_store.persist(
        operation_id,
        "execution-contract",
        json.dumps(contract, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        artifact_type="contract",
    )
    lock_ref = artifact_store.persist(
        operation_id,
        "primary-lock",
        json.dumps(lock, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        artifact_type="lock",
    )
    decision_ref = artifact_store.persist(
        operation_id,
        "analysis-decision",
        json.dumps(decision, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        artifact_type="analysis",
    )
    artifact_mappings = [
        contract_ref.to_mapping(),
        lock_ref.to_mapping(),
        decision_ref.to_mapping(),
    ]

    # 7. Complete the operation with result hash and artifact references
    operation_store.complete(
        operation_id,
        {"contract_sha256": contract_sha, "decision": aflow_decision},
        artifacts=artifact_mappings,
    )

    result["lock_state"] = {
        "present": True,
        "valid": True,
        "locked_by": locked_by,
    }
    result["contract_sha256"] = contract_sha
    result["state_path"] = str(state_path)
    result["operation_status"] = "completed"
    result["artifacts"] = artifact_mappings
    return result
