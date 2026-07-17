from __future__ import annotations

from copy import deepcopy

from aflow.adapters.semantic_reviewer import StaticSemanticReviewer
from aflow.analysis.decision_engine import analyze
from aflow.fixtures.factory import fixed_clock


def test_clean_plan_proceeds_unchanged(clean_request):
    original = deepcopy(clean_request)
    result = analyze(clean_request, clock=fixed_clock)
    assert result["decision"] == "no_material_gap"
    assert result["execution_ready"] is True
    assert result["findings"] == []
    assert clean_request == original


def test_deterministic_failure_skips_semantic_adapter(clean_request):
    class ExplodingReviewer:
        def review(self, request):
            raise AssertionError("semantic reviewer must not run")

    clean_request["plan"]["actions"][0]["phase"] = "design"
    result = analyze(clean_request, ExplodingReviewer(), clock=fixed_clock)
    assert result["decision"] == "material_gap_found"
    assert result["findings"][0]["gap_type"] == "sequence_activation_gap"


def test_schema_gap_has_exact_missing_pointer(clean_request):
    del clean_request["plan"]["actions"][0]["activation"]
    result = analyze(clean_request, clock=fixed_clock)
    assert result["decision"] == "material_gap_found"
    assert "/plan/actions/0/activation" in result["findings"][0]["plan_locations"]


def test_environment_mismatch_is_specific_and_substantiated(clean_request):
    clean_request["plan"]["validations"][0]["required_environment"] = "mock-runtime"
    clean_request["evidence"][0]["claim"] = "Mock runtime passes but production host was not exercised."
    from aflow.storage.hashing import seal
    clean_request["evidence"][0] = seal(clean_request["evidence"][0])
    result = analyze(clean_request, clock=fixed_clock)
    assert result["decision"] == "missing_evidence"
    finding = result["findings"][0]
    assert finding["gap_type"] == "output_environment_gap"
    assert finding["evidence_references"]


def test_vague_false_concern_is_rejected_without_reducing_readiness(clean_request):
    ev = clean_request["evidence"][0]
    candidate = {
        "schema_version": "1.0.0", "finding_id": "finding.vague", "gap_type": "validation_evidence_gap",
        "origin": "semantic", "severity": "blocking", "blocking": True,
        "requirement_references": ["requirement.durable-output"], "plan_locations": ["/validations/0"],
        "specific_claim": "Validation might be incomplete", "evidence_references": [{"evidence_id": ev["evidence_id"], "content_hash": ev["content_hash"]}],
        "reasoning": "Only one validation exists.", "why_it_matters": "More tests could be written.",
        "required_closure": {"closure_code": "add_sufficient_validation", "description": "Add a test.", "acceptance_predicates": ["Two validations exist."]},
        "status": "open",
    }
    result = analyze(clean_request, StaticSemanticReviewer((candidate,)), clock=fixed_clock)
    assert result["decision"] == "no_material_gap"
    assert len(result["rejected_findings"]) == 1


def test_specific_invented_concern_with_unrelated_evidence_is_rejected(clean_request):
    ev = clean_request["evidence"][0]
    candidate = {
        "schema_version": "1.0.0", "finding_id": "finding.invented-timeout", "gap_type": "system_boundary_gap",
        "origin": "semantic", "severity": "blocking", "blocking": True,
        "requirement_references": ["requirement.durable-output"], "plan_locations": ["/actions/0"],
        "specific_claim": "The production request timeout is limited to ten milliseconds and will truncate every response.",
        "evidence_references": [{"evidence_id": ev["evidence_id"], "content_hash": ev["content_hash"]}],
        "reasoning": "A ten millisecond timeout cannot support the required production response.",
        "why_it_matters": "Every production request would fail before output is observable.",
        "required_closure": {"closure_code": "restore_system_boundary", "description": "Use the required production timeout.", "acceptance_predicates": ["Production timeout supports the observable response."]},
        "status": "open",
    }
    result = analyze(clean_request, StaticSemanticReviewer((candidate,)), clock=fixed_clock)
    assert result["decision"] == "no_material_gap"
    assert "does not materially support" in result["rejected_findings"][0]["reason"]


def test_broken_references_cycles_and_coverage_block(clean_request):
    second = deepcopy(clean_request["plan"]["actions"][0])
    second["action_id"] = "action.second"
    second["expected_outputs"][0]["output_id"] = "output.second"
    second["depends_on"] = ["action.write-output"]
    clean_request["plan"]["actions"][0]["depends_on"] = ["action.second"]
    clean_request["plan"]["actions"].append(second)
    clean_request["plan"]["requirements_coverage"][0]["action_ids"].append("action.unknown")
    result = analyze(clean_request, clock=fixed_clock)
    gaps = {item["gap_type"] for item in result["findings"]}
    assert {"broken_reference", "dependency_gap"}.issubset(gaps)


def test_exact_reference_schema_version_and_hash_mismatches_block(clean_request):
    clean_request["plan"]["authority_bundle_reference"]["schema_id"] = "https://example.invalid/wrong.json"
    result = analyze(clean_request, clock=fixed_clock)
    assert any(item["gap_type"] == "broken_reference" for item in result["findings"])


def test_evidence_hash_and_reference_mismatches_block(clean_request):
    clean_request["evidence"][0]["claim"] = "Tampered without resealing."
    clean_request["plan"]["assumptions"] = [{
        "assumption_id": "assumption.bad-ref", "statement": "Depends on missing evidence.",
        "status": "substantiated", "evidence_refs": [{"evidence_id": "evidence.missing", "content_hash": "sha256:" + "0" * 64}],
    }]
    result = analyze(clean_request, clock=fixed_clock)
    assert result["decision"] == "material_gap_found"
    assert sum(item["gap_type"] == "broken_reference" for item in result["findings"]) >= 2


def test_duplicate_domain_ids_become_blocking_finding_not_internal_failure(clean_request):
    clean_request["plan"]["actions"].append(deepcopy(clean_request["plan"]["actions"][0]))
    result = analyze(clean_request, clock=fixed_clock)
    assert result["decision"] == "material_gap_found"
    assert any("duplicate action IDs" in item["specific_claim"] for item in result["findings"])


def test_compatible_deferred_phase_entry_action_is_valid(clean_request):
    capability = clean_request["authority_evidence"]["capabilities"][0]
    capability["activation"]["currently_authorized"] = False
    from aflow.storage.hashing import seal, artifact_ref
    clean_request["authority_evidence"] = seal(clean_request["authority_evidence"])
    clean_request["plan"]["authority_bundle_reference"] = artifact_ref(
        clean_request["authority_evidence"], "authority-evidence.schema.json", id_field="authority_bundle_id"
    )
    action = clean_request["plan"]["actions"][0]
    action["activation"]["currently_authorized"] = False
    action["prerequisites"][0]["status"] = "deferred"
    result = analyze(clean_request, clock=fixed_clock)
    assert result["decision"] == "no_material_gap"


def test_unknown_proof_requirement_and_capability_authority_source_block(clean_request):
    clean_request["success_definition"]["proof_obligations"][0]["requirement_ids"] = ["requirement.unknown"]
    from aflow.storage.hashing import canonical_hash, seal, artifact_ref
    clean_request["success_definition"]["confirmation"]["content_hash"] = canonical_hash(clean_request["success_definition"])
    clean_request["plan"]["success_definition_reference"] = artifact_ref(
        clean_request["success_definition"], "success-definition.schema.json", id_field="success_definition_id"
    )
    clean_request["authority_evidence"]["capabilities"][0]["activation"]["source_authority_id"] = "authority.missing"
    clean_request["authority_evidence"] = seal(clean_request["authority_evidence"])
    clean_request["plan"]["authority_bundle_reference"] = artifact_ref(
        clean_request["authority_evidence"], "authority-evidence.schema.json", id_field="authority_bundle_id"
    )
    result = analyze(clean_request, clock=fixed_clock)
    assert result["blocking"] is True
    assert {item["gap_type"] for item in result["findings"]} >= {"broken_reference", "sequence_activation_gap"}


def test_explicitly_unsubstantiated_assumption_is_not_lost_to_evidence_filter(clean_request):
    clean_request["plan"]["assumptions"] = [{
        "assumption_id": "assumption.restart", "statement": "Restart recovery is already implemented.",
        "status": "unsubstantiated", "evidence_refs": [],
    }]
    result = analyze(clean_request, clock=fixed_clock)
    assert result["decision"] == "missing_evidence"
    assert any(item["gap_type"] == "unsupported_assumption" for item in result["findings"])


def test_action_parent_and_output_paths_cannot_widen_authority(clean_request):
    parent = deepcopy(clean_request)
    parent["plan"]["actions"][0]["target_paths"] = ["src"]
    result = analyze(parent, clock=fixed_clock)
    assert any(item["finding_id"].startswith("authority.action-target") for item in result["findings"])

    output = deepcopy(clean_request)
    output["plan"]["actions"][0]["expected_outputs"][0]["artifact_path"] = "private/result.txt"
    result = analyze(output, clock=fixed_clock)
    assert any(item["finding_id"].startswith("authority.output-target") for item in result["findings"])
