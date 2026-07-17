from __future__ import annotations

from typing import Any

from aflow.storage.hashing import artifact_ref


def classify(event: dict[str, Any], *, affected_requirements: list[str] | None = None) -> dict[str, Any]:
    classes = {item["classification"] for item in event["changes"]}
    kinds = {item["change_type"] for item in event["changes"]}
    if not event["changes"]:
        decision, blocking, reason = "no_drift", False, "Locked and current baselines are identical."
    elif "unknown" in classes or "baseline_unverifiable" in kinds:
        decision, blocking, reason = "baseline_unverifiable", True, "Drift relevance cannot be determined safely."
    elif "protected" in classes:
        decision, blocking, reason = "full_reanalysis_required", True, "Protected authority or configuration changed."
    elif "relevant" in classes:
        decision, blocking, reason = "scoped_revalidation_required", True, "A locked target, dependency, validation, or proof path changed."
    else:
        decision, blocking, reason = "nonblocking_drift_recorded", False, "Only certainly unrelated out-of-scope paths changed."
    return {
        "schema_version": "1.0.0",
        "drift_event_reference": artifact_ref(event, "drift-event.schema.json", id_field="drift_event_id"),
        "decision": decision,
        "blocking": blocking,
        "affected_requirements": affected_requirements or [],
        "affected_plan_locations": ["/repository_baseline_reference"] if blocking else [],
        "reason": reason,
    }

