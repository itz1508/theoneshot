from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from aflow.storage.hashing import artifact_ref
from aflow.domain.models import validate_domain_invariants


Clock = Callable[[], datetime]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _conservative_classification(old: dict[str, Any] | None, new: dict[str, Any] | None) -> str:
    values = {(old or {}).get("classification"), (new or {}).get("classification")}
    if "protected" in values:
        return "protected"
    if "unknown" in values:
        return "unknown"
    if "relevant" in values:
        return "relevant"
    return "unrelated"


def compare_baselines(
    locked: dict[str, Any], current: dict[str, Any], *, clock: Clock = _now
) -> dict[str, Any]:
    validate_domain_invariants(locked, "baseline")
    validate_domain_invariants(current, "baseline")
    before = {item["path"]: item for item in locked["entries"]}
    after = {item["path"]: item for item in current["entries"]}
    changes = []
    if locked["repository_root"] != current["repository_root"]:
        changes.append({
            "path": "repository-root", "change_type": "baseline_unverifiable", "classification": "unknown",
            "before_hash": None, "after_hash": None,
        })
    if locked["scope"] != current["scope"]:
        changes.append({
            "path": "baseline-scope", "change_type": "baseline_unverifiable", "classification": "unknown",
            "before_hash": None, "after_hash": None,
        })
    for path in sorted(before.keys() | after.keys()):
        old, new = before.get(path), after.get(path)
        if old and new and old["content_hash"] == new["content_hash"] and old["classification"] == new["classification"]:
            continue
        if old and new and old["content_hash"] == new["content_hash"]:
            changes.append({
                "path": path, "change_type": "baseline_unverifiable", "classification": "unknown",
                "before_hash": old["content_hash"], "after_hash": new["content_hash"],
            })
            continue
        classification = _conservative_classification(old, new)
        changes.append({
            "path": path,
            "change_type": "added" if old is None else "deleted" if new is None else "modified",
            "classification": classification,
            "before_hash": old["content_hash"] if old else None,
            "after_hash": new["content_hash"] if new else None,
        })
    old_authority = {item["authority_id"]: item["content_hash"] for item in locked["authority_hashes"]}
    new_authority = {item["authority_id"]: item["content_hash"] for item in current["authority_hashes"]}
    for authority_id in sorted(old_authority.keys() | new_authority.keys()):
        if old_authority.get(authority_id) != new_authority.get(authority_id):
            changes.append({
                "path": f"authority/{authority_id}", "change_type": "authority_changed",
                "classification": "protected", "before_hash": old_authority.get(authority_id),
                "after_hash": new_authority.get(authority_id),
            })
    return {
        "schema_version": "1.0.0",
        "drift_event_id": f"drift.{current['baseline_id']}",
        "baseline_reference": artifact_ref(locked, "baseline.schema.json", id_field="baseline_id"),
        "current_baseline": current,
        "changes": changes,
        "observed_at": clock().astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
