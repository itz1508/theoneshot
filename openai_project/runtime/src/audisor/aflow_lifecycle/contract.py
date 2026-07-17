"""Deterministic primary-Codex controls around frozen A-Flow artifacts."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping


QUALIFYING_TASK_KINDS = frozenset(
    {
        "implementation",
        "repair",
        "refactor",
        "dependency_change",
        "configuration_change",
        "integration",
        "schema_change",
        "test_change",
        "build_change",
        "repository_mutation",
    }
)
FROZEN_ANALYSIS_READY = "no_material_gap"
FROZEN_FINAL_PROVEN = "proven"
FROZEN_TO_CONTRACT_READINESS = {
    "no_material_gap": "no_material_gap",
    "material_gap_found": "revision_required",
    "missing_evidence": "uncertainty",
    "contradicted": "contradicted",
    "drift_revalidation_required": "drift_revalidation_required",
}


class AflowLifecycleError(RuntimeError):
    """Raised when an A-Flow lifecycle transition cannot safely proceed."""


def requires_aflow_analysis(task_kind: str) -> bool:
    """Return whether a classified task must complete the A-Flow preflight."""
    return task_kind in QUALIFYING_TASK_KINDS


def frozen_readiness_decision(readiness: str) -> str:
    """Map the locked-contract readiness language to the frozen A-Flow enum."""
    for frozen, contract in FROZEN_TO_CONTRACT_READINESS.items():
        if contract == readiness:
            return frozen
    raise AflowLifecycleError("A-Flow contract readiness decision is unknown")


def normalize_frozen_readiness(aflow_decision: str) -> dict[str, str]:
    """Preserve a frozen decision and its explicit locked-contract meaning."""
    try:
        return {
            "aflow_decision": aflow_decision,
            "contract_decision": FROZEN_TO_CONTRACT_READINESS[aflow_decision],
        }
    except KeyError as exc:
        raise AflowLifecycleError("Frozen A-Flow readiness decision is unknown") from exc


def canonical_text(value: Any) -> str:
    """Produce UTF-8-safe, LF-normalized deterministic canonical text."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _lock_content(analysis: Mapping[str, Any]) -> Mapping[str, Any]:
    required = {
        "immutable_user_task_canonical_text",
        "accepted_plan_canonical_text",
        "success_definition_canonical_text",
        "required_trajectory_canonical_text",
        "validation_cases_canonical_text",
        "fixture_specifications_canonical_text",
        "hash_algorithm",
    }
    payload = analysis.get("lock_payload")
    if not isinstance(payload, Mapping) or set(payload) != required:
        raise AflowLifecycleError("A-Flow lock payload is missing or malformed")
    if payload["hash_algorithm"] != "sha256":
        raise AflowLifecycleError("A-Flow lock payload must use sha256")
    if any(not isinstance(payload[key], str) for key in required - {"hash_algorithm"}):
        raise AflowLifecycleError("A-Flow canonical lock fields must be text")
    return dict(payload)


def accept_for_primary(analysis: Mapping[str, Any], *, execution_contract_sha256: str | None = None) -> dict[str, Any]:
    """Accept a ready analysis and compute the primary-owned canonical lock."""
    decision = analysis.get("decision")
    if not isinstance(decision, Mapping):
        raise AflowLifecycleError("A-Flow readiness decision is missing")
    aflow_decision = decision.get("aflow_decision")
    contract_decision = decision.get("contract_decision")
    if not isinstance(aflow_decision, str) or not isinstance(contract_decision, str):
        raise AflowLifecycleError("A-Flow readiness must retain frozen and contract decisions")
    if normalize_frozen_readiness(aflow_decision)["contract_decision"] != contract_decision:
        raise AflowLifecycleError("A-Flow readiness decision mapping is inconsistent")
    if aflow_decision != FROZEN_ANALYSIS_READY:
        raise AflowLifecycleError("A-Flow analysis is not ready for primary decision")
    if decision.get("plan_ready_for_primary_decision") is not True:
        raise AflowLifecycleError("A-Flow did not provide a primary-ready analysis")
    if analysis.get("plan_gaps") not in ([], None):
        raise AflowLifecycleError("A-Flow analysis retains unresolved plan gaps")
    content = dict(_lock_content(analysis))
    if execution_contract_sha256 is not None:
        if not isinstance(execution_contract_sha256, str) or len(execution_contract_sha256) != 64 or any(char not in "0123456789abcdef" for char in execution_contract_sha256):
            raise AflowLifecycleError("execution contract SHA-256 is malformed")
        content["execution_contract_sha256"] = execution_contract_sha256
    canonical = canonical_text(content)
    return {
        "lock_version": 1,
        "locked_by": "primary_codex",
        "hash_algorithm": "sha256",
        "canonical_payload": content,
        "lock_hash": _sha256(canonical),
    }


def verify_lock(lock: Mapping[str, Any]) -> bool:
    """Verify the primary-owned lock without trusting a stored boolean."""
    try:
        if lock.get("locked_by") != "primary_codex" or lock.get("hash_algorithm") != "sha256":
            return False
        content = lock["canonical_payload"]
        if not isinstance(content, Mapping):
            return False
        return lock.get("lock_hash") == _sha256(canonical_text(content))
    except (AttributeError, TypeError):
        return False


def write_lock(path: Path, lock: Mapping[str, Any]) -> None:
    """Atomically persist only a verified, primary-owned lock."""
    if not verify_lock(lock):
        raise AflowLifecycleError("Refusing to store an unverifiable A-Flow lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(lock, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def completion_allowed(post_build_evaluation: Mapping[str, Any]) -> bool:
    """Only a frozen A-Flow proven result permits a completion claim."""
    return post_build_evaluation.get("state") == FROZEN_FINAL_PROVEN


def frozen_tree_digest(root: Path) -> str:
    """Hash every frozen source file while excluding local runtime by-products."""
    ignored = {".venv", ".pytest_cache", "__pycache__"}
    rows: list[str] = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root)
        if any(part in ignored for part in relative.parts):
            continue
        rows.append(f"{relative.as_posix()}\0{hashlib.sha256(path.read_bytes()).hexdigest()}")
    return hashlib.sha256(("\n".join(rows) + "\n").encode("utf-8")).hexdigest()
