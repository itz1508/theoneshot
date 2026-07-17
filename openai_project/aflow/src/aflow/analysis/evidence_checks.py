from __future__ import annotations

from typing import Any

from aflow.storage.hashing import canonical_hash, verify_hash


GAP_TO_CLOSURE = {
    "structural_missing": "add_required_field",
    "structural_invalid": "correct_invalid_value",
    "broken_reference": "repair_reference",
    "coverage_gap": "cover_requirement",
    "dependency_gap": "resolve_dependency",
    "unsupported_assumption": "substantiate_or_remove_assumption",
    "capability_gap": "assign_valid_capability",
    "system_boundary_gap": "restore_system_boundary",
    "sequence_activation_gap": "correct_sequence_or_activation",
    "validation_evidence_gap": "add_sufficient_validation",
    "output_environment_gap": "exercise_required_environment",
    "authority_ambiguity": "resolve_authority_ownership",
    "contradiction": "resolve_contradiction",
    "relevant_drift": "revalidate_after_drift",
}


def evidence_ref(evidence_id: str, value: Any) -> dict[str, str]:
    content_hash = value.get("content_hash") if isinstance(value, dict) else None
    return {"evidence_id": evidence_id, "content_hash": content_hash or canonical_hash(value)}


def request_evidence_ref(request: dict[str, Any], preferred: str | None = None) -> dict[str, str]:
    evidence = request.get("evidence", [])
    if preferred:
        for item in evidence:
            if item["evidence_id"] == preferred:
                return evidence_ref(item["evidence_id"], item)
    if evidence:
        item = evidence[0]
        return evidence_ref(item["evidence_id"], item)
    repository = request.get("repository_evidence", {})
    return evidence_ref(repository.get("repository_evidence_id", "evidence.repository"), repository)


def make_finding(
    finding_id: str,
    gap_type: str,
    origin: str,
    plan_locations: list[str],
    claim: str,
    evidence_references: list[dict[str, str]],
    *,
    requirement_references: list[str] | None = None,
    reasoning: str | None = None,
    why: str | None = None,
    acceptance_predicates: list[str] | None = None,
) -> dict[str, Any]:
    closure = GAP_TO_CLOSURE.get(gap_type, "other_bounded")
    return {
        "schema_version": "1.0.0",
        "finding_id": finding_id,
        "gap_type": gap_type,
        "origin": origin,
        "severity": "blocking",
        "blocking": True,
        "requirement_references": requirement_references or [],
        "plan_locations": plan_locations or [""],
        "specific_claim": claim,
        "evidence_references": evidence_references,
        "reasoning": reasoning or claim,
        "why_it_matters": why or "The accepted plan could not reliably prove the locked success definition.",
        "required_closure": {
            "closure_code": closure,
            "description": f"Resolve the bounded {gap_type} at the challenged plan location.",
            "acceptance_predicates": acceptance_predicates or [f"The {gap_type} condition is no longer present."],
        },
        "status": "open",
    }


def check(request: dict[str, Any]) -> list[dict[str, Any]]:
    """Verify artifact hashes and every evidence reference carried by the plan."""
    findings: list[dict[str, Any]] = []
    evidence_items = request["evidence"]
    evidence_by_id = {item["evidence_id"]: item for item in evidence_items}
    duplicate_ids = sorted({item["evidence_id"] for item in evidence_items if sum(
        other["evidence_id"] == item["evidence_id"] for other in evidence_items
    ) > 1})
    base_ref = [request_evidence_ref(request)]

    def add(fid: str, location: str, claim: str) -> None:
        findings.append(make_finding(fid, "broken_reference", "deterministic", [location], claim, base_ref))

    for duplicate in duplicate_ids:
        add(f"evidence.duplicate.{duplicate}", "/evidence", f"Evidence ID {duplicate} is duplicated in the bounded input.")
    artifacts = [
        (request["authority_evidence"], "/authority_evidence/content_hash", "authority evidence"),
        (request["repository_evidence"], "/repository_evidence/content_hash", "repository evidence"),
        (request["baseline"], "/baseline/content_hash", "repository baseline"),
    ]
    for artifact, location, label in artifacts:
        if not verify_hash(artifact):
            add(f"hash.{label.replace(' ', '-')}", location, f"The supplied {label} content hash does not match canonical content.")
    for index, item in enumerate(evidence_items):
        if not verify_hash(item):
            add(f"hash.evidence.{index}", f"/evidence/{index}/content_hash", f"Evidence {item['evidence_id']} has a content-hash mismatch.")

    references: list[tuple[str, dict[str, str]]] = []
    plan = request["plan"]
    for i, action in enumerate(plan["actions"]):
        for j, prerequisite in enumerate(action["prerequisites"]):
            for ref in prerequisite.get("evidence_refs", []):
                references.append((f"/plan/actions/{i}/prerequisites/{j}/evidence_refs", ref))
    for field in ("assumptions", "authority_risks"):
        for i, item in enumerate(plan[field]):
            for ref in item.get("evidence_refs", []):
                references.append((f"/plan/{field}/{i}/evidence_refs", ref))
    for index, (location, ref) in enumerate(references):
        actual = evidence_by_id.get(ref["evidence_id"])
        if actual is None or actual["content_hash"] != ref["content_hash"]:
            add(f"evidence.reference.{index}", location, f"Evidence reference {ref['evidence_id']} is absent or hash-mismatched.")
    return findings
