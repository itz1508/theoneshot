from __future__ import annotations

from typing import Any

from aflow.storage.hashing import artifact_ref

from .evidence_checks import make_finding, request_evidence_ref


def check(request: dict[str, Any]) -> list[dict[str, Any]]:
    plan = request["plan"]
    success = request["success_definition"]
    baseline = request["baseline"]
    authority = request["authority_evidence"]
    evidence = [request_evidence_ref(request)]
    findings: list[dict[str, Any]] = []

    expected_success = artifact_ref(success, "success-definition.schema.json", id_field="success_definition_id")
    ref = plan["success_definition_reference"]
    for field, expected in expected_success.items():
        if ref[field] != expected:
            findings.append(make_finding(
                f"reference.success.{field}", "broken_reference", "deterministic",
                [f"/plan/success_definition_reference/{field}"],
                f"Plan success-definition {field} does not match the separately confirmed artifact.", evidence,
                acceptance_predicates=[f"success_definition_reference.{field} equals {expected}"],
            ))

    pairs = [
        ("repository_baseline_reference", baseline, "baseline_id", "baseline.schema.json"),
        ("authority_bundle_reference", authority, "authority_bundle_id", "authority-evidence.schema.json"),
    ]
    for field, artifact, id_field, schema_name in pairs:
        ref = plan[field]
        expected = artifact_ref(artifact, schema_name, id_field=id_field)
        if ref != expected:
            findings.append(make_finding(
                f"reference.{field}", "broken_reference", "deterministic", [f"/plan/{field}"],
                f"Plan {field} does not exactly bind the supplied artifact ID, schema ID, version, and content hash.", evidence,
            ))
    return findings
