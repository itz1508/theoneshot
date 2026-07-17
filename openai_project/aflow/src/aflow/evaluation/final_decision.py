from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from aflow.domain.models import validate_domain_invariants
from aflow.storage.hashing import artifact_ref, seal
from aflow.schemas.validator import validate
from aflow.drift.compare import compare_baselines
from aflow.drift.classification import classify

from .quality import aggregate_quality
from .requirement_trace import build_trace


Clock = Callable[[], datetime]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def evaluate_result(
    *,
    locked_plan: dict[str, Any],
    locked_baseline: dict[str, Any],
    post_build_baseline: dict[str, Any],
    success_definition: dict[str, Any],
    plan: dict[str, Any],
    build_result: dict[str, Any],
    evidence: list[dict[str, Any]],
    clock: Clock = _now,
) -> tuple[dict[str, Any], dict[str, Any]]:
    validate_domain_invariants(locked_plan, "locked_plan")
    validate_domain_invariants(success_definition, "success_definition")
    validate_domain_invariants(plan, "plan")
    validate_domain_invariants(build_result, "build_result")
    validate_domain_invariants(locked_baseline, "baseline")
    validate_domain_invariants(post_build_baseline, "baseline")
    evidence_ids: set[str] = set()
    for item in evidence:
        validate_domain_invariants(item, "evidence")
        if item["evidence_id"] in evidence_ids:
            raise ValueError(f"duplicate build evidence ID: {item['evidence_id']}")
        evidence_ids.add(item["evidence_id"])
    if build_result["locked_plan_reference"] != artifact_ref(locked_plan, "locked-plan.schema.json", id_field="lock_id"):
        raise ValueError("build result does not bind the supplied locked plan")
    if locked_plan["plan_reference"] != artifact_ref(plan, "plan.schema.json", id_field="plan_id"):
        raise ValueError("locked plan does not bind the supplied plan")
    if locked_plan["success_definition_reference"] != artifact_ref(
        success_definition, "success-definition.schema.json", id_field="success_definition_id"
    ):
        raise ValueError("locked plan does not bind the supplied success definition")
    if locked_plan["baseline_reference"] != artifact_ref(locked_baseline, "baseline.schema.json", id_field="baseline_id"):
        raise ValueError("locked plan does not bind the supplied locked baseline")
    if build_result["post_build_baseline_reference"] != artifact_ref(post_build_baseline, "baseline.schema.json", id_field="baseline_id"):
        raise ValueError("build result does not bind the supplied post-build baseline")
    requirement_ids = {item["requirement_id"] for item in success_definition["requirements"]}
    validation_ids = {item["validation_id"] for item in plan["validations"]}
    outcome_ids = {item["outcome_id"] for item in success_definition["prohibited_outcomes"]}
    if any(not set(item["requirement_ids"]).issubset(requirement_ids) for item in build_result["outputs"]):
        raise ValueError("build output references a requirement outside the locked success definition")
    if any(item["validation_id"] not in validation_ids for item in build_result["validation_results"]):
        raise ValueError("build result references an unknown locked validation")
    if any(item["outcome_id"] not in outcome_ids for item in build_result["prohibited_outcome_checks"]):
        raise ValueError("build result references an unknown prohibited outcome")
    drift_event = compare_baselines(locked_baseline, post_build_baseline, clock=clock)
    validate(drift_event, "drift-event.schema.json")
    drift_decision = classify(
        drift_event,
        affected_requirements=sorted(requirement_ids) if drift_event["changes"] else [],
    )
    validate(drift_decision, "drift-decision.schema.json")

    trace = build_trace(
        locked_plan=locked_plan, success_definition=success_definition, plan=plan,
        build_result=build_result, evidence=evidence,
    )
    priority = {item["requirement_id"]: item["priority"] for item in success_definition["requirements"]}
    prohibited = [item["outcome_id"] for item in build_result["prohibited_outcome_checks"] if item["present"]]
    blocking_entries = [item for item in trace["entries"] if priority[item["requirement_id"]] == "blocking"]
    failures = []
    for item in blocking_entries:
        if item["status"] != "proven":
            failures.append({
                "failure_id": f"failure.{item['requirement_id']}",
                "requirement_ids": [item["requirement_id"]],
                "reason": f"Blocking requirement is {item['status']}.",
                "evidence_references": item["evidence_references"],
            })
    drift_blocking = bool(drift_decision and drift_decision["blocking"])
    if drift_blocking:
        for entry in trace["entries"]:
            entry["status"] = "invalidated_by_drift"
            entry["rationale"] = "Relevant, protected, or unknown drift invalidated the locked analysis."
        trace = seal(trace)
        failures.append({
            "failure_id": "failure.relevant-drift", "requirement_ids": [item["requirement_id"] for item in blocking_entries],
            "reason": drift_decision["reason"], "evidence_references": [],
        })
        decision = "invalidated_by_drift"
    elif prohibited or any(item["status"] == "contradicted" for item in blocking_entries):
        decision = "contradicted"
        if prohibited and not failures:
            failures.append({"failure_id": "failure.prohibited-outcome", "requirement_ids": [], "reason": "A prohibited outcome is present.", "evidence_references": []})
    elif not failures and all(item["status"] == "proven" for item in trace["entries"]):
        decision = "proven"
    elif failures:
        decision = "unproven"
    else:
        decision = "partially_proven"
    blocking = decision in {"unproven", "contradicted", "invalidated_by_drift"}
    if blocking and not failures:
        failures.append({"failure_id": "failure.output", "requirement_ids": [], "reason": "Returned output is not proven.", "evidence_references": []})
    validate_domain_invariants(trace, "requirement_trace")
    evaluation = {
        "schema_version": "1.0.0",
        "evaluation_id": f"evaluation.{build_result['build_result_id']}",
        "locked_plan_reference": artifact_ref(locked_plan, "locked-plan.schema.json", id_field="lock_id"),
        "build_result_reference": artifact_ref(build_result, "build-result.schema.json", id_field="build_result_id"),
        "trace_reference": artifact_ref(trace, "requirement-evidence-trace.schema.json", id_field="trace_id"),
        "decision": decision,
        "blocking": blocking,
        "quality_summary": aggregate_quality(trace["entries"]),
        "blocking_failures": failures,
        "prohibited_outcomes_present": prohibited,
        "decided_at": clock().astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    return trace, validate_domain_invariants(seal(evaluation), "final_evaluation")
