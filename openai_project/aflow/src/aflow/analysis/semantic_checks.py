from __future__ import annotations

from typing import Any

from .evidence_checks import make_finding, request_evidence_ref


def candidates(request: dict[str, Any]) -> list[dict[str, Any]]:
    plan = request["plan"]
    success = request["success_definition"]
    proof_by_id = {item["proof_id"]: item for item in success["proof_obligations"]}
    evidence_items = request.get("evidence", [])
    result: list[dict[str, Any]] = []

    for index, assumption in enumerate(plan["assumptions"]):
        if assumption["status"] == "unsubstantiated":
            refs = assumption.get("evidence_refs") or [request_evidence_ref(request)]
            result.append(make_finding(
                f"semantic.assumption.{assumption['assumption_id']}", "unsupported_assumption", "semantic",
                [f"/plan/assumptions/{index}"],
                f"Assumption {assumption['assumption_id']} is explicitly unsubstantiated: {assumption['statement']}", refs,
                reasoning="The plan labels this premise unsubstantiated and provides no verified basis for depending on it.",
                acceptance_predicates=["The assumption is either removed or supported by bounded matching evidence."],
            ))

    for index, validation in enumerate(plan["validations"]):
        proof = proof_by_id.get(validation["proof_id"])
        if not proof:
            continue
        proof_env = proof.get("required_environment")
        validation_env = validation.get("required_environment")
        if proof_env and validation_env != proof_env:
            matching = next((item for item in evidence_items if any(
                word in item.get("claim", "").lower() for word in ("mock", "substitute", "same process", "environment")
            )), None)
            ref = request_evidence_ref(request, matching["evidence_id"] if matching else None)
            result.append(make_finding(
                f"semantic.environment.{validation['validation_id']}", "output_environment_gap", "semantic",
                [f"/plan/validations/{index}/required_environment"],
                f"Validation {validation['validation_id']} uses {validation_env or 'an unspecified environment'} while proof {validation['proof_id']} requires {proof_env}.",
                [ref], requirement_references=validation["requirement_ids"],
                reasoning="The visible validation boundary differs materially from the proof obligation's required environment.",
                acceptance_predicates=[f"Validation evidence is collected in the required environment: {proof_env}."],
            ))
        required_types = set(proof.get("required_evidence_types", []))
        actual_types = set(validation["evidence_expected"])
        proof_mismatch = validation["proof_type"] != proof["proof_type"] or validation["executor"] != proof["executor"]
        if (required_types and not required_types.issubset(actual_types)) or proof_mismatch:
            differences = sorted(required_types - actual_types)
            detail = f"omits required evidence types {differences}" if differences else "changes the proof type or executor boundary"
            result.append(make_finding(
                f"semantic.evidence.{validation['validation_id']}", "validation_evidence_gap", "semantic",
                [f"/plan/validations/{index}/evidence_expected"],
                f"Validation {validation['validation_id']} {detail} relative to proof {validation['proof_id']}.",
                [request_evidence_ref(request)], requirement_references=validation["requirement_ids"],
                reasoning="Its planned evidence cannot satisfy the independently defined proof obligation.",
            ))
    return result
