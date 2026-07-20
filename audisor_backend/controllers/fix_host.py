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
        policy = self.policy_reader()
        accepted_task = {"findings": _plain(operation.findings), "manifest": _plain(operation.manifest), "statements": _plain(operation.statements)}
        accepted_plan = _plain(operation.plan)
        repository_context = {
            "authority": operation.authority_context,
            "baseline_evidence": {"workspace": operation.workspace_identity},
            "accepted_constraints": {"operation_type": "fix"},
            "required_outputs": operation.manifest.files,
        }
        workspace_identity = operation.workspace_identity
        use_fix_local_boundary = self.aflow_igniter is ignite
        analysis_package = None
        if policy.enabled and self.aflow_igniter is ignite and not use_fix_local_boundary:
            try:
                analysis_package = package_from_context(
                    operation_id=operation.operation_id,
                    operation_type="fix",
                    accepted_task=accepted_task,
                    accepted_plan=accepted_plan,
                    authority_context=operation.authority_context,
                    repository_context={
                        **repository_context,
                        "aflow_analysis_request": operation.aflow_analysis_request,
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
                    operation_id=operation.operation_id,
                    operation_type="fix",
                    accepted_task=accepted_task,
                    accepted_plan=accepted_plan,
                    repository_context=repository_context,
                    workspace_identity=workspace_identity,
                    authority_context=operation.authority_context,
                )
                artifact = audisor_operation_artifact(context, policy, status="package_validation_failed", error=exc)
                self.store.persist(operation.operation_id, artifact)
                return finalize_unresolved(operation, artifact)
        context = make_operation_context(
            operation_id=operation.operation_id,
            operation_type="fix",
            accepted_task=accepted_task,
            accepted_plan=accepted_plan,
            repository_context=repository_context,
            workspace_identity=workspace_identity,
            authority_context=operation.authority_context,
            analysis_package=analysis_package,
        )
        if not policy.enabled:
            artifact = audisor_operation_artifact(context, policy, status="skipped_disabled")
            self.store.persist(operation.operation_id, artifact)
            return continue_implementation(operation, artifact)
        worker = self.worker_factory(policy.base_url, policy.model_id, timeout_seconds=policy.timeout_seconds)
        try:
            if use_fix_local_boundary:
                result = invoke_local_fix(worker, operation.plan, operation.findings, operation.manifest)
            else:
                result = self.aflow_igniter(operation_context=context, policy=policy, worker=worker)
        except Exception as exc:
            artifact = audisor_operation_artifact(context, policy, status="provider_failed" if getattr(exc, "code", "").startswith("provider") else "validation_failed", error=exc)
            self.store.persist(operation.operation_id, artifact)
            return finalize_unresolved(operation, artifact)
        artifact = audisor_operation_artifact(context, policy, status="accepted" if result.implementation_eligible else "rejected", result=result)
        if result.implementation_eligible:
            artifact["handoff_path"] = self.store.persist_handoff(operation, result)
        self.store.persist(operation.operation_id, artifact)
        if not result.implementation_eligible:
            return finalize_unresolved(operation, artifact)
        return continue_implementation(operation, result)
