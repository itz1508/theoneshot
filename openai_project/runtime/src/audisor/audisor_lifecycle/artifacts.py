"""Canonical Audisor operation artifact construction."""

from __future__ import annotations

import os
import tempfile
from typing import Any, Mapping

from .operation import AudisorOperationContext, FrozenAudisorPolicy

FAILURE_CODE_BY_STATUS = {
    "package_validation_failed": "package_persistence_failed",
    "provider_failed": "provider_failed",
    "validation_failed": "final_schema_failed",
    "rejected": "decision_inconsistent",
}


def _evidence_helpers():
    from audisor.builder.evidence import canonical_json_bytes, sanitize_text, sha256_bytes

    return canonical_json_bytes, sha256_bytes, sanitize_text


def _artifact_root(context: AudisorOperationContext):
    from pathlib import Path

    path = context.workspace_identity.get("path")
    if not isinstance(path, str) or not path:
        raise ValueError("artifact root is unavailable")
    root = Path(path) / "audisor-artifacts"
    root.mkdir(parents=True, exist_ok=True)
    return root


def persist_audisor_stage(context: AudisorOperationContext, name: str, value: Mapping[str, Any]) -> str:
    """Persist one canonical stage artifact atomically before its consumer."""
    root = _artifact_root(context)
    target = root / f"{name}.json"
    canonical_json_bytes, _sha256_bytes, _sanitize_text = _evidence_helpers()
    payload = canonical_json_bytes(value) + b"\n"
    fd, temporary_name = tempfile.mkstemp(prefix=f".{name}.", suffix=".tmp", dir=root)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, target)
    except Exception:
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise
    return str(target)


def audisor_operation_artifact(
    context: AudisorOperationContext,
    policy: FrozenAudisorPolicy,
    *,
    status: str,
    result: Any = None,
    error: BaseException | None = None,
    raw_response_sha256: str | None = None,
) -> dict[str, Any]:
    analysis = result.build_analysis if result is not None else None
    contract = result.execution_contract if result is not None and analysis is None else None
    accepted = bool(result is not None and analysis is None and result.implementation_eligible)
    decision = None
    if isinstance(contract, Mapping):
        readiness = contract.get("readiness")
        if isinstance(readiness, Mapping):
            decision = readiness.get("aflow_decision")
    error_body = None
    failure_code = None
    if error is not None:
        _canonical_json_bytes, _sha256_bytes, sanitize_text = _evidence_helpers()
        message, _ = sanitize_text(error, limit=1000)
        failure_code = getattr(error, "code", None) or FAILURE_CODE_BY_STATUS.get(status, type(error).__name__)
        error_body = {
            "code": failure_code,
            "message": message,
            "detail": getattr(error, "detail", ""),
            "missing_inputs": list(getattr(error, "missing_inputs", [])),
            "gaps": list(getattr(error, "gaps", [])),
            "required_corrections": list(getattr(error, "required_corrections", [])),
            "retry_prompt": getattr(error, "retry_prompt", None),
        }
    canonical_json_bytes, sha256_bytes, _sanitize_text = _evidence_helpers()
    contract_hash = sha256_bytes(canonical_json_bytes(contract)) if contract is not None else None
    return {
        "schema_version": 1,
        "operation_id": context.operation_id,
        "operation_type": context.operation_type,
        "context_sha256": context.context_sha256,
        "policy": {
            "enabled": policy.enabled,
            "provider": policy.provider,
            "model_id": policy.model_id,
            "base_url": policy.base_url,
            "timeout_seconds": policy.timeout_seconds,
        },
        "status": status,
        "failure_code": failure_code,
        "decision": decision,
        "implementation_eligible": accepted,
        "analysis_only": analysis is not None,
        "build_analysis": analysis.model_dump(mode="json") if analysis is not None else None,
        "raw_response_sha256": raw_response_sha256,
        "execution_contract_sha256": contract_hash,
        "error": error_body,
        "authority": {
            "mutation_authorized": False,
            "execution_authorized": False,
            "apply_authorized": False,
            "completion_claimed": False,
        },
    }
