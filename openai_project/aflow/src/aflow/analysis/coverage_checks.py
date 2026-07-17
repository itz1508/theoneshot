from __future__ import annotations

from typing import Any

from .evidence_checks import make_finding, request_evidence_ref


def check(request: dict[str, Any]) -> list[dict[str, Any]]:
    success, plan = request["success_definition"], request["plan"]
    requirement_ids = {item["requirement_id"] for item in success["requirements"]}
    blocking = {item["requirement_id"] for item in success["requirements"] if item["priority"] == "blocking"}
    proof_by_id = {item["proof_id"]: item for item in success["proof_obligations"]}
    action_by_id = {item["action_id"]: item for item in plan["actions"]}
    validation_by_id = {item["validation_id"]: item for item in plan["validations"]}
    coverage_by_req = {item["requirement_id"]: item for item in plan["requirements_coverage"]}
    evidence = [request_evidence_ref(request)]
    findings: list[dict[str, Any]] = []

    def add(fid: str, gap: str, loc: str, claim: str, reqs: list[str] | None = None) -> None:
        findings.append(make_finding(fid, gap, "deterministic", [loc], claim, evidence, requirement_references=reqs))

    all_requirement_refs: list[tuple[str, str]] = []
    for i, row in enumerate(plan["requirements_coverage"]):
        all_requirement_refs.append((row["requirement_id"], f"/plan/requirements_coverage/{i}/requirement_id"))
    for i, action in enumerate(plan["actions"]):
        all_requirement_refs.extend((req, f"/plan/actions/{i}/requirement_ids") for req in action["requirement_ids"])
    for i, validation in enumerate(plan["validations"]):
        all_requirement_refs.extend((req, f"/plan/validations/{i}/requirement_ids") for req in validation["requirement_ids"])
    for req, loc in all_requirement_refs:
        if req not in requirement_ids:
            add(f"coverage.unknown.{len(findings)+1}", "broken_reference", loc, f"Unknown requirement reference {req}.")

    for proof_index, proof in enumerate(success["proof_obligations"]):
        for requirement_index, req in enumerate(proof["requirement_ids"]):
            if req not in requirement_ids:
                add(
                    f"proof.requirement.{proof['proof_id']}.{requirement_index}", "broken_reference",
                    f"/success_definition/proof_obligations/{proof_index}/requirement_ids/{requirement_index}",
                    f"Proof {proof['proof_id']} references unknown requirement {req}.",
                )

    for req in sorted(blocking):
        row = coverage_by_req.get(req)
        if row is None:
            add(f"coverage.missing.{req}", "coverage_gap", "/plan/requirements_coverage", f"Blocking requirement {req} has no coverage row.", [req])
            continue
        if any(action_id not in action_by_id for action_id in row["action_ids"]):
            add(f"coverage.action.{req}", "broken_reference", "/plan/requirements_coverage", f"Coverage for {req} references an unknown action.", [req])
        if any(validation_id not in validation_by_id for validation_id in row["validation_ids"]):
            add(f"coverage.validation.{req}", "broken_reference", "/plan/requirements_coverage", f"Coverage for {req} references an unknown validation.", [req])
        covered_actions = [item for item in action_by_id.values() if req in item["requirement_ids"]]
        covered_validations = [item for item in validation_by_id.values() if req in item["requirement_ids"]]
        if not covered_actions or not covered_validations:
            add(f"coverage.empty.{req}", "coverage_gap", "/plan/requirements_coverage", f"Blocking requirement {req} needs at least one action and validation.", [req])

    for index, validation in enumerate(plan["validations"]):
        proof = proof_by_id.get(validation["proof_id"])
        if proof is None:
            add(f"proof.unknown.{validation['validation_id']}", "broken_reference", f"/plan/validations/{index}/proof_id", f"Validation {validation['validation_id']} references unknown proof {validation['proof_id']}.")
            continue
        if not set(validation["requirement_ids"]).issubset(set(proof["requirement_ids"])):
            add(f"proof.coverage.{validation['validation_id']}", "coverage_gap", f"/plan/validations/{index}/requirement_ids", "Validation requirements are not covered by its proof obligation.", validation["requirement_ids"])
        for action_id in validation["action_ids"]:
            if action_id not in action_by_id:
                add(f"validation.action.{validation['validation_id']}", "broken_reference", f"/plan/validations/{index}/action_ids", f"Validation references unknown action {action_id}.")
    return findings
