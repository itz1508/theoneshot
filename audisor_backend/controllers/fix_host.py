"""Small accepted-operation host for the existing A-Flow Fix controller."""

from __future__ import annotations

import json
import hashlib
import os
import tempfile
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Callable

from audisor.audisor_lifecycle.artifacts import audisor_operation_artifact
from audisor.audisor_lifecycle.analysis_package import package_from_context
from audisor.audisor_lifecycle.ignition import ignite
from audisor.audisor_lifecycle.operation import AudisorOperationContext, FrozenAudisorPolicy, make_operation_context, read_frozen_audisor_policy
from audisor.workers.local import LocalWorker

from audisor_backend.schemas.fix.models import FixScopedManifest, FindingsList, ImplementationPlan, Statement
from audisor_backend.adapters.aflow_fix import invoke_local_fix
from audisor_backend.scanning.dependency_closure import resolve_dependency_details


@dataclass(frozen=True)
class DependencyResolutionResult:
    """Deterministic result of resolving one dependency finding against the repository."""
    finding_id: str
    resolved: bool
    resolved_paths: tuple[str, ...]
    evidence_records: tuple[dict[str, str], ...]
    failure_reason: str | None


@dataclass(frozen=True)
class AcceptedFixOperation:
    operation_id: str
    findings: FindingsList
    manifest: FixScopedManifest
    statements: tuple[Statement, Statement, Statement]
    plan: ImplementationPlan
    workspace_identity: dict[str, Any]
    authority_context: dict[str, Any]
    aflow_analysis_request: dict[str, Any] | None = None


class FixOperationStore:
    """Minimal atomic operation record for Fix idempotency and A-Flow evidence."""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, operation_id: str) -> Path:
        return self.root / f"{operation_id}.json"

    def load(self, operation_id: str) -> dict[str, Any] | None:
        path = self.path(operation_id)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def persist(self, operation_id: str, artifact: dict[str, Any]) -> None:
        target = self.path(operation_id)
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(artifact, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, target)

    def persist_handoff(self, operation: AcceptedFixOperation, result: Any) -> str:
        """Persist the qualified Fix handoff before implementation continues.

        Includes the verification_contract and verification_grounding from
        the accepted Fix analysis response so post-execution verification
        has concrete, grounded success criteria.
        """
        root = self.root / operation.operation_id
        root.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 1,
            "operation_id": operation.operation_id,
            "operation_type": "fix",
            "findings": _plain(operation.findings),
            "scoped_manifest": _plain(operation.manifest),
            "statements": _plain(operation.statements),
            "qualified_plan": _plain(getattr(result, "candidate_plan", operation.plan)),
            "authority": {
                "mutation_authorized": False,
                "execution_authorized": False,
                "apply_authorized": False,
                "completion_claimed": False,
            },
        }
        # Persist the verification contract from the accepted analysis result.
        success_definition = getattr(result, "success_definition", None)
        if success_definition is not None:
            payload["verification_contract"] = {
                "finding_checks": _plain(success_definition.finding_checks),
                "validations": _plain(success_definition.validations),
                "must_not_regress": _plain(success_definition.must_not_regress),
                "success_rule": success_definition.success_rule,
            }
        # Persist the verification grounding evidence.
        verification_grounding = getattr(result, "verification_grounding", None)
        if verification_grounding is not None:
            payload["verification_grounding"] = verification_grounding.to_mapping()
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
        payload["handoff_sha256"] = hashlib.sha256(encoded).hexdigest()
        target = root / "qualified-fix-handoff.json"
        temporary = target.with_suffix(".tmp")
        temporary.write_bytes(json.dumps(payload, sort_keys=True, indent=2).encode("utf-8") + b"\n")
        os.replace(temporary, target)
        return str(target)


def _plain(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if hasattr(value, "__dict__"):
        return {key: _plain(item) for key, item in value.__dict__.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    return value


def _prepare_dependency_evidence(
    operation: AcceptedFixOperation,
    repository_root: str,
) -> tuple[AcceptedFixOperation, list[DependencyResolutionResult]]:
    """Resolve repository-local Python dependency gaps before model evaluation.

    Calls the existing ``resolve_dependency_details()`` to discover local
    imports from finding-affected files.  Produces per-finding resolution
    results and an enriched ``FixScopedManifest`` with updated
    ``dependency_closure`` and ``dependency_evidence``.

    Original findings are never mutated.  Dependency closure adds readable
    context only — it does not expand mutation authority.
    """
    from pathlib import Path

    root = Path(repository_root)
    # Resolve dependencies from the repository (best-effort; files may not exist)
    try:
        new_closure, new_evidence = resolve_dependency_details(root, operation.findings)
    except (FileNotFoundError, OSError):
        new_closure, new_evidence = [], {}

    # Merge with existing manifest values (deduplicate, preserve existing)
    existing_closure = set(operation.manifest.dependency_closure)
    existing_evidence: dict[str, list[dict[str, str]]] = {
        path: list(records) for path, records in operation.manifest.dependency_evidence.items()
    }
    for path in new_closure:
        existing_closure.add(path)
    for path, records in new_evidence.items():
        if path not in existing_evidence:
            existing_evidence[path] = []
        existing_ids = {
            (rec.get("originating_finding_id"), rec.get("dependency_source"), rec.get("dependency_target"))
            for rec in existing_evidence[path]
        }
        for rec in records:
            key = (rec.get("originating_finding_id"), rec.get("dependency_source"), rec.get("dependency_target"))
            if key not in existing_ids:
                existing_evidence[path].append(rec)
                existing_ids.add(key)

    merged_closure = sorted(existing_closure)
    merged_evidence = {path: records for path, records in sorted(existing_evidence.items())}

    # Build enriched manifest
    enriched_manifest = FixScopedManifest(
        files=list(operation.manifest.files),
        dependency_closure=merged_closure,
        input_hash=operation.manifest.input_hash,
        file_hashes=dict(operation.manifest.file_hashes),
        dependency_evidence=merged_evidence,
    )

    # Build per-finding resolution results
    resolution_results: list[DependencyResolutionResult] = []
    for finding in operation.findings:
        if finding.type != "dependency.unresolved":
            resolution_results.append(DependencyResolutionResult(
                finding_id=finding.id,
                resolved=True,  # non-dependency findings are not gated here
                resolved_paths=(),
                evidence_records=(),
                failure_reason=None,
            ))
            continue

        # Check if any evidence record references this finding
        resolved_paths: list[str] = []
        evidence_records: list[dict[str, str]] = []
        for path, records in merged_evidence.items():
            for rec in records:
                if rec.get("originating_finding_id") == finding.id:
                    if path not in resolved_paths:
                        resolved_paths.append(path)
                    evidence_records.append(rec)

        if resolved_paths:
            resolution_results.append(DependencyResolutionResult(
                finding_id=finding.id,
                resolved=True,
                resolved_paths=tuple(resolved_paths),
                evidence_records=tuple(evidence_records),
                failure_reason=None,
            ))
        else:
            # Attempted resolution but no repository-local target found
            module = finding.evidence.get("module", "unknown") if isinstance(finding.evidence, dict) else "unknown"
            resolution_results.append(DependencyResolutionResult(
                finding_id=finding.id,
                resolved=False,
                resolved_paths=(),
                evidence_records=(),
                failure_reason=f"no repository-local target found for import {module!r} from {finding.file}",
            ))

    enriched_operation = AcceptedFixOperation(
        operation_id=operation.operation_id,
        findings=operation.findings,
        manifest=enriched_manifest,
        statements=operation.statements,
        plan=operation.plan,
        workspace_identity=operation.workspace_identity,
        authority_context=operation.authority_context,
        aflow_analysis_request=operation.aflow_analysis_request,
    )
    return enriched_operation, resolution_results


class AcceptedFixDispatcher:
    def __init__(self, store: FixOperationStore, *, policy_reader=read_frozen_audisor_policy, aflow_igniter=ignite, worker_factory=LocalWorker):
        self.store = store
        self.policy_reader = policy_reader
        self.aflow_igniter = aflow_igniter
        self.worker_factory = worker_factory

    def dispatch(self, operation: AcceptedFixOperation, continue_implementation: Callable[[AcceptedFixOperation, Any], Any], finalize_unresolved: Callable[[AcceptedFixOperation, dict[str, Any]], Any]) -> Any:
        existing = self.store.load(operation.operation_id)
        if existing is not None:
            return existing
        try:
            operation.manifest.validate()
            if not operation.manifest.file_hashes:
                raise ValueError("scoped_snapshot_required")
            if operation.manifest.input_hash != operation.manifest.input_hash.strip():
                raise ValueError("invalid_snapshot_hash")
        except Exception as exc:
            artifact = {
                "operation_id": operation.operation_id,
                "operation_type": "fix",
                "status": "validation_failed",
                "implementation_eligible": False,
                "error": {"code": "scoped_snapshot_required", "message": str(exc)},
            }
            self.store.persist(operation.operation_id, artifact)
            return finalize_unresolved(operation, artifact)
        try:
            operation.plan.validate(operation.manifest)
        except Exception as exc:
            artifact = {"operation_id": operation.operation_id, "operation_type": "fix", "status": "validation_failed", "implementation_eligible": False, "error": {"code": "invalid_fix_plan", "message": str(exc)}}
            self.store.persist(operation.operation_id, artifact)
            return finalize_unresolved(operation, artifact)

        # --- Deterministic dependency preparation (before model evaluation) ---
        repository_root = operation.workspace_identity.get("root", str(Path.cwd()))
        enriched_operation, resolution_results = _prepare_dependency_evidence(operation, repository_root)

        policy = self.policy_reader()
        accepted_task = {"findings": _plain(enriched_operation.findings), "manifest": _plain(enriched_operation.manifest), "statements": _plain(enriched_operation.statements)}
        accepted_plan = _plain(enriched_operation.plan)
        repository_context = {
            "authority": enriched_operation.authority_context,
            "baseline_evidence": {"workspace": enriched_operation.workspace_identity},
            "accepted_constraints": {"operation_type": "fix"},
            "required_outputs": enriched_operation.manifest.files,
        }
        workspace_identity = enriched_operation.workspace_identity
        use_fix_local_boundary = self.aflow_igniter is ignite
        analysis_package = None
        if policy.enabled and self.aflow_igniter is ignite and not use_fix_local_boundary:
            try:
                analysis_package = package_from_context(
                    operation_id=enriched_operation.operation_id,
                    operation_type="fix",
                    accepted_task=accepted_task,
                    accepted_plan=accepted_plan,
                    authority_context=enriched_operation.authority_context,
                    repository_context={
                        **repository_context,
                        "aflow_analysis_request": enriched_operation.aflow_analysis_request,
                    },
                    workspace_identity=workspace_identity,
                    provider_policy={
                        "provider": policy.provider,
                        "base_url": policy.base_url,
                        "model_id": policy.model_id,
                        "timeout_seconds": policy.timeout_seconds,
                    },
                )
            except Exception as exc:
                context = make_operation_context(
                    operation_id=enriched_operation.operation_id,
                    operation_type="fix",
                    accepted_task=accepted_task,
                    accepted_plan=accepted_plan,
                    repository_context=repository_context,
                    workspace_identity=workspace_identity,
                    authority_context=enriched_operation.authority_context,
                )
                artifact = audisor_operation_artifact(context, policy, status="package_validation_failed", error=exc)
                self.store.persist(enriched_operation.operation_id, artifact)
                return finalize_unresolved(enriched_operation, artifact)
        context = make_operation_context(
            operation_id=enriched_operation.operation_id,
            operation_type="fix",
            accepted_task=accepted_task,
            accepted_plan=accepted_plan,
            repository_context=repository_context,
            workspace_identity=workspace_identity,
            authority_context=enriched_operation.authority_context,
            analysis_package=analysis_package,
        )
        if not policy.enabled:
            artifact = audisor_operation_artifact(context, policy, status="skipped_disabled")
            self.store.persist(enriched_operation.operation_id, artifact)
            return continue_implementation(enriched_operation, artifact)

        # --- Model evaluation with enriched manifest ---
        worker = self.worker_factory(policy.base_url, policy.model_id, timeout_seconds=policy.timeout_seconds)
        try:
            if use_fix_local_boundary:
                result = invoke_local_fix(worker, enriched_operation.plan, enriched_operation.findings, enriched_operation.manifest)
            else:
                result = self.aflow_igniter(operation_context=context, policy=policy, worker=worker)
        except Exception as exc:
            artifact = audisor_operation_artifact(context, policy, status="provider_failed" if getattr(exc, "code", "").startswith("provider") else "validation_failed", error=exc)
            self.store.persist(enriched_operation.operation_id, artifact)
            return finalize_unresolved(enriched_operation, artifact)

        # --- Completeness evaluation using resolution evidence ---
        from audisor_backend.policies.fix.completeness import evaluate_fix_completeness
        candidate_plan = getattr(result, "candidate_plan", enriched_operation.plan)
        completeness = evaluate_fix_completeness(
            manifest=enriched_operation.manifest,
            plan=candidate_plan,
            findings=enriched_operation.findings,
            resolution_results=resolution_results,
        )

        if completeness.status != "pass":
            # Block: completeness not satisfied after deterministic evidence pass
            blocked_artifact = {
                "operation_id": enriched_operation.operation_id,
                "operation_type": "fix",
                "status": "blocked",
                "implementation_eligible": False,
                "unresolved_reason": "information_gap",
                "completeness_status": completeness.status,
                "missing_info": completeness.missing_info,
                "dependency_resolution": [
                    {
                        "finding_id": r.finding_id,
                        "resolved": r.resolved,
                        "resolved_paths": list(r.resolved_paths),
                        "failure_reason": r.failure_reason,
                    }
                    for r in resolution_results
                ],
                "attempted_resolution_count": 1,
            }
            self.store.persist(enriched_operation.operation_id, blocked_artifact)
            return finalize_unresolved(enriched_operation, blocked_artifact)

        artifact = audisor_operation_artifact(context, policy, status="accepted" if result.implementation_eligible else "rejected", result=result)
        if result.implementation_eligible:
            artifact["handoff_path"] = self.store.persist_handoff(enriched_operation, result)
        self.store.persist(enriched_operation.operation_id, artifact)
        if not result.implementation_eligible:
            return finalize_unresolved(enriched_operation, artifact)
        return continue_implementation(enriched_operation, result)
