from __future__ import annotations

from datetime import datetime
from typing import Any

from aflow.adapters.semantic_reviewer import SemanticReviewer
from aflow.analysis.decision_engine import Clock, analyze, utc_now
from aflow.domain.models import validate_domain_invariants
from aflow.storage.hashing import artifact_ref, seal


def _pointer_value(root: Any, pointer: str) -> Any:
    current = root
    scoped = pointer.removeprefix("/plan") if pointer.startswith("/plan") else pointer
    if not scoped:
        return current
    for raw in scoped.lstrip("/").split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        current = current[int(token)] if isinstance(current, list) else current[token]
    return current


def _predicate_gate(
    finding: dict[str, Any], *, changed: bool, added_evidence: bool,
    revised_request: dict[str, Any], replayed_semantic_review: bool,
) -> tuple[bool, str]:
    code = finding["required_closure"]["closure_code"]
    predicates = finding["required_closure"]["acceptance_predicates"]
    if finding["origin"] == "semantic" and not (
        replayed_semantic_review or finding["finding_id"].startswith("semantic.")
    ):
        return False, "The original external semantic reviewer was not replayed, so its predicates remain unverified."
    if code == "exercise_required_environment":
        success = revised_request["success_definition"]
        proof_by_id = {item["proof_id"]: item for item in success["proof_obligations"]}
        valid = any(
            validation.get("required_environment") == proof_by_id.get(validation["proof_id"], {}).get("required_environment")
            for validation in revised_request["plan"]["validations"]
            if set(validation["requirement_ids"]) & set(finding["requirement_references"])
        )
        return valid and (changed or added_evidence), f"Evaluated {len(predicates)} original predicate(s) against proof environment and bounded revision evidence."
    evidence_codes = {"substantiate_or_remove_assumption", "add_sufficient_validation", "resolve_contradiction", "revalidate_after_drift"}
    change_codes = {
        "add_required_field", "correct_invalid_value", "repair_reference", "cover_requirement", "resolve_dependency",
        "align_terminology", "correct_interpretation", "assign_valid_capability", "restore_system_boundary",
        "resolve_overlap", "correct_sequence_or_activation", "resolve_authority_ownership", "other_bounded",
    }
    if code in evidence_codes:
        return changed or added_evidence, f"Evaluated {len(predicates)} original predicate(s) against changed structure and added evidence."
    if code in change_codes:
        return changed, f"Evaluated {len(predicates)} original predicate(s) against the challenged plan location."
    return False, "No deterministic evaluator exists for the original closure predicate code."


def close_findings(
    closure_request: dict[str, Any],
    *,
    prior_decision: dict[str, Any],
    original_plan: dict[str, Any],
    revised_analysis_request: dict[str, Any],
    reviewer: SemanticReviewer | None = None,
    clock: Clock = utc_now,
) -> dict[str, Any]:
    validate_domain_invariants(closure_request, "closure_request")
    validate_domain_invariants(prior_decision, "analysis_decision")
    validate_domain_invariants(original_plan, "plan")
    if closure_request["revised_plan"] != revised_analysis_request["plan"]:
        raise ValueError("closure revised_plan must equal the plan submitted for reanalysis")
    expected_prior = artifact_ref(prior_decision, "analysis-decision.schema.json", id_field="analysis_id")
    expected_original = artifact_ref(original_plan, "plan.schema.json", id_field="plan_id")
    expected_success = artifact_ref(
        revised_analysis_request["success_definition"], "success-definition.schema.json", id_field="success_definition_id"
    )
    if closure_request["prior_decision_reference"] != expected_prior:
        raise ValueError("closure request does not exactly bind the supplied prior decision")
    if closure_request["original_plan_reference"] != expected_original:
        raise ValueError("closure request does not exactly bind the supplied original plan")
    if prior_decision["plan_reference"] != closure_request["original_plan_reference"]:
        raise ValueError("closure request does not bind the prior decision's original plan")
    if prior_decision["success_definition_reference"] != expected_success:
        raise ValueError("revision changed or weakened the locked success definition")
    if closure_request["revised_plan"]["success_definition_reference"] != prior_decision["success_definition_reference"]:
        raise ValueError("revised plan no longer binds the original success definition")
    added_by_id = {item["evidence_id"]: item for item in closure_request["added_evidence"]}
    revised_by_id = {item["evidence_id"]: item for item in revised_analysis_request["evidence"]}
    if any(item.get("visibility") != "closure_input" for item in closure_request["added_evidence"]):
        raise ValueError("added closure evidence must have closure_input visibility")
    if any(revised_by_id.get(evidence_id) != item for evidence_id, item in added_by_id.items()):
        raise ValueError("every added closure evidence artifact must be present unchanged in revised analysis input")
    new_decision = analyze(revised_analysis_request, reviewer, clock=clock)
    added_refs = [
        {"evidence_id": item["evidence_id"], "content_hash": item["content_hash"]}
        for item in closure_request["added_evidence"]
    ]
    results = []
    for finding in prior_decision["findings"]:
        changed = False
        for location in finding["plan_locations"]:
            try:
                changed = changed or _pointer_value(original_plan, location) != _pointer_value(closure_request["revised_plan"], location)
            except (KeyError, IndexError, TypeError, ValueError):
                changed = True
        gap_remains = any(
            candidate["gap_type"] == finding["gap_type"]
            and (
                set(candidate["plan_locations"]) & set(finding["plan_locations"])
                or set(candidate["requirement_references"]) & set(finding["requirement_references"])
            )
            for candidate in new_decision["findings"]
        )
        predicates_satisfied, predicate_reason = _predicate_gate(
            finding, changed=changed, added_evidence=bool(added_refs), revised_request=revised_analysis_request,
            replayed_semantic_review=reviewer is not None,
        )
        if not gap_remains and predicates_satisfied and new_decision["decision"] == "no_material_gap":
            status = "closed"
            reason = f"{predicate_reason} The original gap is absent and reanalysis found no new blocking gap."
        elif not gap_remains and predicates_satisfied:
            status = "partially_closed"
            reason = f"{predicate_reason} The original gap is absent, but reanalysis found a different blocking gap."
        elif not gap_remains:
            status = "open"
            reason = f"The finding was not reproduced, but closure remains open: {predicate_reason}"
        else:
            status = "open"
            reason = "The original gap remains after evaluating its original acceptance predicates."
        results.append({
            "finding_id": finding["finding_id"],
            "status": status,
            "evidence_references": added_refs,
            "reason": reason,
        })
    result = {
        "schema_version": "1.0.0",
        "closure_id": closure_request["closure_id"],
        "prior_decision_reference": closure_request["prior_decision_reference"],
        "revised_plan_reference": artifact_ref(closure_request["revised_plan"], "plan.schema.json", id_field="plan_id"),
        "finding_results": results,
        "new_decision": new_decision,
    }
    return validate_domain_invariants(seal(result), "closure_result")
