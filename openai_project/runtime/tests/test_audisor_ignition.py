from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from audisor.audisor_lifecycle.adapter import assemble_contract
from audisor.audisor_lifecycle.contract import AudisorLifecycleError, frozen_tree_digest
from audisor.audisor_lifecycle.ignition import _execution_ready, ignite
from audisor.audisor_lifecycle.analysis_package import assemble_analysis_package
from audisor.audisor_lifecycle.operation import FrozenAudisorPolicy, make_operation_context


FIXTURES = Path(__file__).parent / "fixtures" / "aflow_contract"


def source(name: str = "ready-input.json") -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def invoke_from(template: dict, calls: list | None = None):
    def invoke(task, candidate, context, **kwargs):
        if calls is not None:
            calls.append((task, candidate, context))
        result = copy.deepcopy(template)
        result["candidate_implementation_plan"] = candidate
        return result
    return invoke


def test_01_mutation_selects_the_audisor_lifecycle() -> None:
    result = ignite(task_kind="implementation", task={"id": "t"}, repository_context={}, supplied_plan=source()["candidate_implementation_plan"], invoke_audisor_analysis=invoke_from(source()))
    assert result.lifecycle_selected


def test_02_read_only_does_not_select_full_lifecycle() -> None:
    result = ignite(task_kind="factual_question", task={"id": "t"}, repository_context={})
    assert not result.lifecycle_selected and result.execution_contract is None


def test_03_supplied_plan_is_reused_without_factory_call() -> None:
    supplied = source()["candidate_implementation_plan"]
    result = ignite(task_kind="repair", task={}, repository_context={}, supplied_plan=supplied, create_candidate_plan=lambda _: pytest.fail("factory called"), invoke_audisor_analysis=invoke_from(source()))
    assert result.candidate_plan is supplied and result.candidate_plan_source == "supplied"


def test_04_missing_plan_creates_one_before_aflow() -> None:
    calls: list = []
    candidate = source()["candidate_implementation_plan"]
    result = ignite(task_kind="refactor", task={"id": "t"}, repository_context={"root": "repo"}, create_candidate_plan=lambda task: candidate, invoke_audisor_analysis=invoke_from(source(), calls))
    assert result.candidate_plan_source == "created" and calls[0][1] is candidate


def test_05_aflow_is_invoked_after_plan_and_before_contract_assembly() -> None:
    calls: list = []
    result = ignite(task_kind="integration", task={"id": "t"}, repository_context={"authority": "active"}, supplied_plan=source()["candidate_implementation_plan"], invoke_audisor_analysis=invoke_from(source(), calls))
    assert calls and result.execution_contract is not None


def test_06_aflow_result_is_passed_to_existing_adapter() -> None:
    result = ignite(task_kind="test_change", task={}, repository_context={}, supplied_plan=source()["candidate_implementation_plan"], invoke_audisor_analysis=invoke_from(source()))
    direct = assemble_contract(source())["aflow_execution_contract"]
    assert result.execution_contract == direct


def test_07_ready_valid_contract_allows_implementation() -> None:
    result = ignite(task_kind="configuration_change", task={}, repository_context={}, supplied_plan=source()["candidate_implementation_plan"], invoke_audisor_analysis=invoke_from(source()))
    assert result.implementation_eligible


def test_08_nonready_decision_prevents_implementation() -> None:
    value = source("nonready-input.json")
    result = ignite(task_kind="repository_mutation", task={}, repository_context={}, supplied_plan=value["candidate_implementation_plan"], invoke_audisor_analysis=invoke_from(value))
    assert not result.implementation_eligible


def test_09_missing_evidence_prevents_implementation() -> None:
    value = source(); value["candidate_implementation_plan"]["evidence_manifest"]["evidence_items"][1]["requirement_ids"] = []
    with pytest.raises(AudisorLifecycleError, match="every requirement needs evidence"):
        ignite(task_kind="implementation", task={}, repository_context={}, supplied_plan=value["candidate_implementation_plan"], invoke_audisor_analysis=invoke_from(value))


def test_10_invalid_traceability_prevents_implementation() -> None:
    value = source(); value["candidate_implementation_plan"]["implementation_plan"][0]["requirement_ids"] = ["missing"]
    with pytest.raises(AudisorLifecycleError, match="unresolved reference"):
        ignite(task_kind="implementation", task={}, repository_context={}, supplied_plan=value["candidate_implementation_plan"], invoke_audisor_analysis=invoke_from(value))


def test_11_tampered_sha256_prevents_implementation() -> None:
    contract = assemble_contract(source())["aflow_execution_contract"]
    contract["lock_payload"]["sha256"] = "0" * 64
    assert not _execution_ready(contract)


def test_12_ignition_delegates_validation_to_adapter() -> None:
    import audisor.audisor_lifecycle.adapter as adapter
    import audisor.audisor_lifecycle.ignition as ignition
    assert ignition.assemble_contract is adapter.assemble_contract
    assert ignition.verify_contract is adapter.verify_contract


def test_13_custom_agent_is_analysis_only() -> None:
    text = (Path(__file__).resolve().parents[1] / "src" / "audisor" / "audisor_lifecycle" / "build_analysis.py").read_text(encoding="utf-8")
    assert "analysis-only" in text and "Do not implement" in text and "execution_contract" in text


def test_14_frozen_tree_is_unchanged() -> None:
    frozen = Path(__file__).resolve().parents[2] / "aflow"
    assert frozen_tree_digest(frozen) == "f0e20a4b7d6c4de71f45ff9dce9df1602c74b750fea6c14255d0cce6df069bb9"


def test_authoritative_boundary_persists_package_before_local_callback(tmp_path, monkeypatch) -> None:
    from test_audisor_analysis_package import frozen_request
    import audisor.audisor_lifecycle.ignition as ignition

    request = frozen_request()
    request["analysis_id"] = "operation.build-001"
    package = assemble_analysis_package(
        operation_id="operation.build-001",
        operation_type="build",
        accepted_task={"objective": "test"},
        accepted_plan={"plan_id": "plan.fixture"},
        authority_context={"authority": "host"},
        analysis_request=request,
        repository_context={"aflow_analysis_request": request},
        workspace_identity={"path": str(tmp_path / "workspace")},
        provider_policy={"provider": "local-openai-compatible"},
    )
    context = make_operation_context(
        operation_id=package.operation_id,
        operation_type="build",
        accepted_task=package.accepted_task,
        accepted_plan=package.accepted_plan,
        repository_context=package.repository_context,
        workspace_identity=package.workspace_identity,
        authority_context=package.authority_context,
        analysis_package=package,
    )
    observed = []

    def callback(task, candidate, repository_context, **kwargs):
        observed.append((Path(context.workspace_identity["path"]) / "audisor-artifacts" / "analysis-package.json").is_file())
        return source()

    monkeypatch.setattr(ignition, "local_invoke_audisor_analysis", callback)
    result = ignite(
        operation_context=context,
        policy=FrozenAudisorPolicy(True, "local-openai-compatible", "qwen2.5-coder:7b", "http://127.0.0.1:11434"),
        worker=object(),
    )
    assert observed == [True]
    assert result.implementation_eligible
    assert (Path(context.workspace_identity["path"]) / "audisor-artifacts" / "execution-contract.json").is_file()
