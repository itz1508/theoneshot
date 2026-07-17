from __future__ import annotations

import json
from pathlib import Path

import pytest

from audisor.aflow_lifecycle.contract import (
    AflowLifecycleError,
    accept_for_primary,
    completion_allowed,
    frozen_tree_digest,
    frozen_readiness_decision,
    normalize_frozen_readiness,
    requires_aflow_analysis,
    verify_lock,
    write_lock,
)
from audisor.aflow_lifecycle.hook import default_state_root, evaluate_hook_payload


def analysis(*, state: str = "no_material_gap", ready: bool = True, gaps: list[object] | None = None) -> dict:
    return {
        "success_definition": {"functional_outcomes": ["automatic A-Flow lifecycle"]},
        "required_trajectory": {"stages": ["plan", "analysis", "lock", "implementation", "evaluation"]},
        "plan_gaps": [] if gaps is None else gaps,
        "validation_cases": [{"case_id": "case.activation"}],
        "fixture_specifications": [{"fixture_id": "fixture.activation", "input": {"task_kind": "implementation"}}],
        "lock_payload": {
            "immutable_user_task_canonical_text": "task\n",
            "accepted_plan_canonical_text": "plan\n",
            "success_definition_canonical_text": "success\n",
            "required_trajectory_canonical_text": "trajectory\n",
            "validation_cases_canonical_text": "validation\n",
            "fixture_specifications_canonical_text": "fixtures\n",
            "hash_algorithm": "sha256",
        },
        "decision": {
            "aflow_decision": frozen_readiness_decision(state),
            "contract_decision": state,
            "plan_ready_for_primary_decision": ready,
        },
    }


@pytest.mark.parametrize("task_kind", ["implementation", "repair", "refactor", "configuration_change", "repository_mutation"])
def test_qualifying_tasks_require_aflow(task_kind: str) -> None:
    assert requires_aflow_analysis(task_kind)


def test_read_only_factual_task_does_not_require_lifecycle() -> None:
    assert not requires_aflow_analysis("factual_question")


def test_non_ready_analysis_prevents_primary_acceptance() -> None:
    with pytest.raises(AflowLifecycleError):
        accept_for_primary(analysis(state="material_gap_found", ready=False, gaps=[{"gap_id": "g1"}]))


def test_missing_evidence_prevents_primary_acceptance() -> None:
    with pytest.raises(AflowLifecycleError):
        accept_for_primary(analysis(state="uncertainty", ready=False))


@pytest.mark.parametrize(
    ("readiness", "frozen"),
    [
        ("no_material_gap", "no_material_gap"),
        ("revision_required", "material_gap_found"),
        ("uncertainty", "missing_evidence"),
    ],
)
def test_contract_readiness_maps_to_frozen_aflow_vocabulary(readiness: str, frozen: str) -> None:
    assert frozen_readiness_decision(readiness) == frozen
    assert normalize_frozen_readiness(frozen) == {
        "aflow_decision": frozen,
        "contract_decision": readiness,
    }


def test_inconsistent_frozen_and_contract_decisions_fail_closed() -> None:
    result = analysis()
    result["decision"]["contract_decision"] = "uncertainty"
    with pytest.raises(AflowLifecycleError):
        accept_for_primary(result)


def test_no_material_gap_does_not_lock_until_primary_accepts() -> None:
    result = analysis()
    assert "lock_hash" not in result
    lock = accept_for_primary(result)
    assert lock["locked_by"] == "primary_codex"


def test_primary_computes_and_stores_a_verifiable_lock(tmp_path: Path) -> None:
    lock = accept_for_primary(analysis())
    destination = tmp_path / "active-lock.json"
    write_lock(destination, lock)
    assert verify_lock(json.loads(destination.read_text(encoding="utf-8")))


def test_agent_definition_is_read_only() -> None:
    agent = Path(__file__).resolve().parents[3] / ".codex" / "agents" / "aflow.toml"
    text = agent.read_text(encoding="utf-8")
    assert 'sandbox_mode = "read-only"' in text
    assert "Do not implement" in text


def test_fixture_specifications_remain_data_only(tmp_path: Path) -> None:
    result = analysis()
    assert result["fixture_specifications"]
    assert list(tmp_path.iterdir()) == []


def test_plan_change_invalidates_the_existing_lock() -> None:
    lock = accept_for_primary(analysis())
    lock["canonical_payload"]["accepted_plan_canonical_text"] = "changed plan\n"
    assert not verify_lock(lock)


def test_material_deviation_requires_a_new_analysis_lock() -> None:
    old = accept_for_primary(analysis())
    revised = analysis()
    revised["lock_payload"]["required_trajectory_canonical_text"] = "revised trajectory\n"
    new = accept_for_primary(revised)
    assert old["lock_hash"] != new["lock_hash"]


def test_malformed_analysis_fails_closed() -> None:
    with pytest.raises(AflowLifecycleError):
        accept_for_primary({"decision": {"aflow_decision": "no_material_gap", "plan_ready_for_primary_decision": True}})


def test_pretool_hook_denies_mutation_without_lock(tmp_path: Path) -> None:
    result = evaluate_hook_payload({"tool_name": "ApplyPatch", "tool_input": {}}, tmp_path)
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_pretool_hook_allows_read_only_work_without_lock(tmp_path: Path) -> None:
    assert evaluate_hook_payload({"tool_name": "Bash", "tool_input": {"command": "git status --short"}}, tmp_path) == {}


def test_hook_default_state_root_is_project_scoped() -> None:
    assert default_state_root() == Path(__file__).resolve().parents[3] / ".codex" / "aflow-state"


def test_post_build_evaluation_runs_before_completion() -> None:
    assert not completion_allowed({"state": "unproven"})


@pytest.mark.parametrize("state", ["unproven", "contradicted", "partially_proven", "invalidated_by_drift"])
def test_non_proven_evaluation_cannot_close_task(state: str) -> None:
    assert not completion_allowed({"state": state})


def test_fully_evidenced_result_allows_completion() -> None:
    assert completion_allowed({"state": "proven"})


def test_frozen_aflow_tree_is_byte_stable() -> None:
    frozen = Path(__file__).resolve().parents[2] / "aflow"
    assert frozen_tree_digest(frozen) == "73bf670908329d7316c4edc8f2cc6ab2115991c6c0674c42530ad3170ee2e8d3"
