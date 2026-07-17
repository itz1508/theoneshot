"""Deterministic contract-adapter proof cases (exactly 18)."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import jsonschema
import pytest

from audisor.aflow_lifecycle.adapter import assemble_contract, verify_contract
from audisor.aflow_lifecycle.contract import AflowLifecycleError


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).parent / "fixtures" / "aflow_contract"
SCHEMA = ROOT / "schemas" / "aflow-execution-contract.schema.json"


def source(name: str = "ready-input.json") -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def assembled(name: str = "ready-input.json") -> dict:
    return assemble_contract(source(name))["aflow_execution_contract"]


def expected(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))["expected_output"]


def invalid(value: dict, message: str) -> None:
    with pytest.raises(AflowLifecycleError, match=message):
        assemble_contract(value)


def test_case_01_ready_fixture_assembles_and_validates_schema() -> None:
    result = {"aflow_execution_contract": assembled()}
    jsonschema.Draft202012Validator(json.loads(SCHEMA.read_text())).validate(result)
    assert verify_contract(result["aflow_execution_contract"])
    output = expected("ready-contract.expected.json")
    assert result["aflow_execution_contract"]["lock_payload"]["sha256"] == output["sha256"]


def test_case_02_nonready_fixture_is_locked_but_execution_is_denied() -> None:
    contract = assembled("nonready-input.json")
    assert verify_contract(contract)
    assert contract["readiness"]["contract_decision"] == "uncertainty"
    assert not contract["readiness"]["execution_permitted_when"]["aflow_decision_is_no_material_gap"]
    assert contract["lock_payload"]["sha256"] == expected("nonready-contract.expected.json")["sha256"]


def test_case_03_preserves_frozen_and_external_decisions() -> None:
    readiness = assembled()["readiness"]
    assert readiness["aflow_decision"] == "no_material_gap"
    assert readiness["contract_decision"] == "no_material_gap"
    assert readiness["decision_mapping"]["missing_evidence"] == "uncertainty"


def test_case_04_rejects_duplicate_requirement_ids() -> None:
    value = source(); value["candidate_implementation_plan"]["success_definition"]["requirements"].append(copy.deepcopy(value["candidate_implementation_plan"]["success_definition"]["requirements"][0]))
    invalid(value, "duplicate requirement_id")


def test_case_05_rejects_unresolved_action_requirement() -> None:
    value = source(); value["candidate_implementation_plan"]["implementation_plan"][0]["requirement_ids"] = ["missing"]
    invalid(value, "unresolved reference")


def test_case_06_rejects_action_missing_from_trajectory() -> None:
    value = source(); value["candidate_implementation_plan"]["execution_trajectory"][0]["exact_actions"] = []
    invalid(value, "stage exact_actions")


def test_case_07_rejects_validation_with_unknown_fixture() -> None:
    value = source(); value["candidate_implementation_plan"]["validation_contract"][0]["fixture_id"] = "missing"
    invalid(value, "fixture reference")


def test_case_08_rejects_fixture_without_reverse_validation_link() -> None:
    value = source(); value["candidate_implementation_plan"]["fixture_specifications"][0]["validation_ids"] = ["validation-schema"]
    invalid(value, "validation-to-fixture")


def test_case_09_rejects_requirement_without_evidence() -> None:
    value = source(); value["candidate_implementation_plan"]["evidence_manifest"]["evidence_items"][1]["requirement_ids"] = []
    invalid(value, "every requirement needs evidence")


def test_case_10_rejects_checkpoint_without_evidence() -> None:
    value = source(); value["candidate_implementation_plan"]["evidence_manifest"]["evidence_items"][1]["checkpoint_ids"] = []
    value["candidate_implementation_plan"]["evidence_manifest"]["evidence_items"][2]["checkpoint_ids"] = ["checkpoint-1"]
    invalid(value, "every trajectory checkpoint needs evidence")


def test_case_11_rejects_requirement_without_final_acceptance_rule() -> None:
    value = source(); value["candidate_implementation_plan"]["post_build_acceptance"]["acceptance_rules"][1]["requirement_ids"] = ["req-schema"]
    invalid(value, "final acceptance rule")


def test_case_12_rejects_preserved_condition_without_state_evidence() -> None:
    value = source(); value["candidate_implementation_plan"]["evidence_manifest"]["state_checks"] = []
    invalid(value, "preserved condition")


def test_case_13_rejects_path_outside_directional_authority() -> None:
    value = source(); value["candidate_implementation_plan"]["implementation_plan"][0]["target_paths"] = ["openai_project/other.py"]
    invalid(value, "outside allowed")


def test_case_14_rejects_conflicting_tool_authority() -> None:
    value = source(); value["authority"]["prohibited_tools"] = ["write_file"]
    invalid(value, "tool lists conflict")


def test_case_15_rejects_evidence_with_unresolved_reference() -> None:
    value = source(); value["candidate_implementation_plan"]["evidence_manifest"]["evidence_items"][0]["validation_ids"] = ["missing"]
    invalid(value, "evidence validation_ids contains an unresolved reference")


def test_case_16_equivalent_unordered_collections_have_identical_text_and_hash() -> None:
    first = assembled()
    value = source()
    plan = value["candidate_implementation_plan"]
    plan["success_definition"]["requirements"].reverse()
    plan["validation_contract"].reverse()
    plan["fixture_specifications"].reverse()
    plan["evidence_manifest"]["evidence_items"].reverse()
    plan["post_build_acceptance"]["acceptance_rules"].reverse()
    second = assemble_contract(value)["aflow_execution_contract"]
    assert first["lock_payload"]["canonical_text"] == second["lock_payload"]["canonical_text"]
    assert first["lock_payload"]["sha256"] == second["lock_payload"]["sha256"]


def test_case_17_ordered_stages_and_actions_retain_semantic_order() -> None:
    contract = assembled()
    body = json.loads(contract["lock_payload"]["canonical_text"])
    assert [item["stage_id"] for item in body["execution_trajectory"]] == ["stage-1", "stage-2"]
    assert [item["action_id"] for item in body["implementation_plan"]] == ["action-schema", "action-trace"]


def test_case_18_rejects_body_text_and_hash_tampering() -> None:
    contract = assembled()
    body_changed = copy.deepcopy(contract); body_changed["authority"]["allowed_paths"] = ["openai_project"]
    text_changed = copy.deepcopy(contract); text_changed["lock_payload"]["canonical_text"] += " "
    hash_changed = copy.deepcopy(contract); hash_changed["lock_payload"]["sha256"] = hashlib.sha256(b"tampered").hexdigest()
    assert not verify_contract(body_changed)
    assert not verify_contract(text_changed)
    assert not verify_contract(hash_changed)
