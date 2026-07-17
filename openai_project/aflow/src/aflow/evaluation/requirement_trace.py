from __future__ import annotations

from typing import Any

from aflow.storage.hashing import artifact_ref, seal

from .quality import DIMENSIONS, explicit_quality, is_trusted_evidence


def build_trace(
    *,
    locked_plan: dict[str, Any],
    success_definition: dict[str, Any],
    plan: dict[str, Any],
    build_result: dict[str, Any],
    evidence: list[dict[str, Any]],
) -> dict[str, Any]:
    evidence_by_id = {item["evidence_id"]: item for item in evidence}
    proof_by_req: dict[str, list[dict[str, Any]]] = {}
    for proof in success_definition["proof_obligations"]:
        for requirement_id in proof["requirement_ids"]:
            proof_by_req.setdefault(requirement_id, []).append(proof)
    validation_by_id = {item["validation_id"]: item for item in plan["validations"]}
    result_by_id = {item["validation_id"]: item for item in build_result["validation_results"]}
    entries = []
    prohibited_present = any(item["present"] for item in build_result["prohibited_outcome_checks"])
    expected_outcomes = {item["outcome_id"] for item in success_definition["prohibited_outcomes"]}
    checked_outcomes = {item["outcome_id"] for item in build_result["prohibited_outcome_checks"]}
    prohibited_absence_evidenced = expected_outcomes == checked_outcomes
    for check in build_result["prohibited_outcome_checks"]:
        for ref in check["evidence_references"]:
            item = evidence_by_id.get(ref["evidence_id"])
            if item is None or item["content_hash"] != ref["content_hash"] or not is_trusted_evidence(item):
                prohibited_absence_evidenced = False

    for requirement in success_definition["requirements"]:
        requirement_id = requirement["requirement_id"]
        outputs = [item for item in build_result["outputs"] if requirement_id in item["requirement_ids"]]
        output_refs = [ref for item in outputs for ref in item["evidence_references"]]
        plans = [item for item in plan["validations"] if requirement_id in item["requirement_ids"]]
        results = [result_by_id.get(item["validation_id"]) for item in plans]
        result_refs = [ref for item in results if item for ref in item["evidence_references"]]
        refs = list({(ref["evidence_id"], ref["content_hash"]): ref for ref in output_refs + result_refs}.values())
        visible = [evidence_by_id[ref["evidence_id"]] for ref in refs if (
            ref["evidence_id"] in evidence_by_id and evidence_by_id[ref["evidence_id"]]["content_hash"] == ref["content_hash"]
        )]
        trusted = [item for item in visible if is_trusted_evidence(item)]
        refs_valid = len(visible) == len(refs) and bool(refs)
        validations_pass = bool(plans) and all(item and item["status"] == "passed" for item in results)
        environment_matches = build_result["observed_environment"].get("matches_required_environment") is True
        required_types = {item for proof in proof_by_req.get(requirement_id, []) for item in proof.get("required_evidence_types", [])}
        observed_types = {item["evidence_type"] for item in trusted}
        types_match = required_types.issubset(observed_types)
        contradicted = build_result["status"] == "failed" or any(
            item["source"].get("evidence_status") == "contradicted" for item in visible
        )
        base_proven = bool(outputs) and validations_pass and refs_valid and environment_matches and types_match and not prohibited_present and prohibited_absence_evidenced
        quality_results = []
        for dimension in DIMENSIONS:
            applicable = dimension in requirement["quality_dimensions"]
            status = explicit_quality(trusted, dimension)
            if dimension == "constraint_compliance" and applicable and prohibited_present:
                status = "fail"
            elif dimension == "evidence_quality" and applicable and status is None:
                status = "pass" if refs_valid and types_match else "unproven"
            elif dimension == "constraint_compliance" and applicable and status is None:
                status = "pass" if prohibited_absence_evidenced and build_result["status"] == "completed" else "unproven"
            elif applicable and status is None:
                status = "pass" if base_proven and dimension in {"correctness", "completeness"} else "unproven"
            elif not applicable:
                status = "not_applicable"
            reason = (
                "Visible evidence explicitly supports this quality dimension." if status == "pass"
                else "This dimension is not applicable to the locked requirement." if status == "not_applicable"
                else "Visible evidence does not prove this required quality dimension." if status in {"unproven", "partial"}
                else "Visible evidence contradicts or fails this required quality dimension."
            )
            quality_results.append({"dimension": dimension, "status": status, "reason": reason, "evidence_references": refs})
        required_statuses = [q["status"] for q in quality_results if q["dimension"] in requirement["quality_dimensions"]]
        if contradicted or "fail" in required_statuses:
            status = "contradicted"
        elif base_proven and all(item == "pass" for item in required_statuses):
            status = "proven"
        elif any(item in {"pass", "partial"} for item in required_statuses) and outputs:
            status = "partially_proven"
        else:
            status = "unproven"
        entries.append({
            "requirement_id": requirement_id,
            "expected_observable_outcome": requirement["observable_outcome"],
            "evidence_references": refs,
            "quality_results": quality_results,
            "status": status,
            "rationale": (
                "All required observable, validation, environment, evidence-type, and quality predicates passed."
                if status == "proven" else
                "The returned build evidence does not satisfy every locked proof and quality predicate."
            ),
        })
    trace = {
        "schema_version": "1.0.0",
        "trace_id": f"trace.{build_result['build_result_id']}",
        "locked_plan_reference": artifact_ref(locked_plan, "locked-plan.schema.json", id_field="lock_id"),
        "build_result_reference": artifact_ref(build_result, "build-result.schema.json", id_field="build_result_id"),
        "entries": entries,
    }
    return seal(trace)
