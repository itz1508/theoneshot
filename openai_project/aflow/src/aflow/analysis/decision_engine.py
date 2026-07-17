from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from aflow.adapters.semantic_reviewer import NullSemanticReviewer, SemanticReviewer
from aflow.domain.models import DomainInvariantError, validate_domain_invariants
from aflow.schemas.validator import validate
from aflow.storage.hashing import artifact_ref, canonical_hash, seal

from . import activation_checks, authority_checks, coverage_checks, dependency_checks, reference_checks
from . import evidence_checks
from .evidence_checks import evidence_ref, make_finding
from .schema_admission import admit
from .semantic_checks import candidates as structured_candidates
from .substantiation import substantiate


Clock = Callable[[], datetime]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(clock: Clock) -> str:
    return clock().astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _schema_findings(request: dict[str, Any], admission: dict[str, Any]) -> list[dict[str, Any]]:
    results = []
    for error in admission["errors"]:
        gap = "structural_missing" if error["keyword"] == "required" else "structural_invalid"
        ref = evidence_ref(error["error_id"], error)
        results.append(make_finding(
            f"finding.{error['error_id']}", gap, "deterministic",
            [error["instance_path"]],
            f"Schema admission failed at {error['instance_path'] or '/'}: {error['message']}", [ref],
            reasoning=f"JSON Schema keyword {error['keyword']} failed at schema location {error['schema_path']}.",
            acceptance_predicates=[f"The value at {error['instance_path'] or '/'} satisfies {error['schema_path']}."],
        ))
    return results


def _decision_type(findings: list[dict[str, Any]]) -> str:
    gaps = {item["gap_type"] for item in findings}
    if "contradiction" in gaps:
        return "contradicted"
    if "relevant_drift" in gaps:
        return "drift_revalidation_required"
    if gaps & {"unsupported_assumption", "validation_evidence_gap", "output_environment_gap"}:
        return "missing_evidence"
    return "material_gap_found"


def analyze(
    request: dict[str, Any],
    reviewer: SemanticReviewer | None = None,
    *,
    clock: Clock = utc_now,
) -> dict[str, Any]:
    admission = admit(request)
    if not admission["valid"]:
        plan = request.get("plan", {"plan_id": "plan.invalid", "version": "1.0.0"})
        success = request.get("success_definition", {"success_definition_id": "success.invalid", "version": "1.0.0", "confirmation": {"content_hash": canonical_hash({})}})
        findings = _schema_findings(request, admission)
        decision = {
            "schema_version": "1.0.0",
            "analysis_id": request.get("analysis_id", "analysis.invalid"),
            "success_definition_reference": {
                "artifact_id": success.get("success_definition_id", "success.invalid"),
                "schema_id": "https://theoneshot.dev/schemas/aflow/v1/success-definition.schema.json",
                "version": success.get("version", "1.0.0"),
                "content_hash": success.get("confirmation", {}).get("content_hash", canonical_hash(success)),
            },
            "plan_reference": {
                "artifact_id": plan.get("plan_id", "plan.invalid"),
                "schema_id": "https://theoneshot.dev/schemas/aflow/v1/plan.schema.json",
                "version": plan.get("version", "1.0.0"),
                "content_hash": canonical_hash(plan),
            },
            "decision": "material_gap_found",
            "blocking": True,
            "execution_ready": False,
            "findings": findings,
            "rejected_findings": [],
            "decided_at": _timestamp(clock),
        }
        return validate_domain_invariants(seal(decision), "analysis_decision")

    deterministic: list[dict[str, Any]] = []
    domain_valid = True
    for field, kind, location in (
        ("success_definition", "success_definition", "/success_definition"),
        ("plan", "plan", "/plan"),
    ):
        try:
            validate_domain_invariants(request[field], kind)
        except DomainInvariantError as exc:
            domain_valid = False
            deterministic.append(make_finding(
                f"domain.{field}", "structural_invalid", "deterministic", [location],
                f"{field} violates a mandatory domain invariant: {exc}",
                [evidence_ref(f"domain.{field}", {"error": str(exc), "location": location})],
                reasoning="The schema-valid artifact still violates a non-bypassable A-Flow domain invariant.",
                acceptance_predicates=[f"The artifact at {location} satisfies all ID, hash, and decision invariants."],
            ))
    if domain_valid:
        for checker in (evidence_checks, reference_checks, coverage_checks, dependency_checks, authority_checks, activation_checks):
            deterministic.extend(checker.check(request))

    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    if not deterministic:
        adapter = reviewer or NullSemanticReviewer()
        review = adapter.review(request)
        validate(review, "semantic-review.schema.json")
        built_in = structured_candidates(request)
        external = []
        for candidate in review["candidate_findings"]:
            if candidate.get("finding_id", "").startswith("semantic."):
                rejected.append({
                    "finding_id": candidate["finding_id"],
                    "reason": "semantic.* is reserved for deterministic local semantic rules",
                    "evidence_references": candidate.get("evidence_references", []),
                })
            else:
                external.append(candidate)
        accepted_built_in, rejected_built_in = substantiate(
            built_in, request, plan_locations_are_evidence=True
        )
        accepted_external, rejected_external = substantiate(external, request)
        accepted = accepted_built_in + accepted_external
        rejected.extend(rejected_built_in + rejected_external)
    findings = deterministic + accepted
    clean = not findings
    decision = {
        "schema_version": "1.0.0",
        "analysis_id": request["analysis_id"],
        "success_definition_reference": artifact_ref(
            request["success_definition"], "success-definition.schema.json", id_field="success_definition_id"
        ),
        "plan_reference": artifact_ref(request["plan"], "plan.schema.json", id_field="plan_id"),
        "decision": "no_material_gap" if clean else _decision_type(findings),
        "blocking": not clean,
        "execution_ready": clean,
        "findings": findings,
        "rejected_findings": rejected,
        "decided_at": _timestamp(clock),
    }
    return validate_domain_invariants(seal(decision), "analysis_decision")
