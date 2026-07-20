from __future__ import annotations

import json
from pathlib import Path

import pytest

from audisor.audisor_lifecycle.analysis_package import assemble_analysis_package
from audisor.audisor_lifecycle.artifacts import persist_audisor_stage
from audisor.audisor_lifecycle.local_invoker import (
    HOST_OWNED_FIELDS,
    AudisorInvocationError,
    _candidate_schema,
    _frozen_canonical_hash,
    invoke_audisor_analysis,
)
from audisor.audisor_lifecycle.operation import make_operation_context
from audisor.schemas.task_output import TaskOutput

from test_audisor_analysis_package import frozen_request


class Worker:
    def __init__(self, answer: str, *, tool: bool = False, finish_reason: str | None = "stop"):
        self.answer = answer
        self.calls = 0
        self.prompts: list[str] = []
        self.tool = tool
        self.finish_reason = finish_reason

    def execute(self, request):
        self.calls += 1
        self.prompts.append(request.prompt)
        return TaskOutput(task_id=request.task_id, answer=self.answer).set_response_metadata(
            http_status=200,
            transport_succeeded=True,
            finish_reason=self.finish_reason,
            tool_call_present=self.tool,
            choice_count=1,
        )


def prepared(tmp_path: Path):
    request = frozen_request()
    request["analysis_id"] = "operation.build-001"
    package = assemble_analysis_package(
        operation_id="operation.build-001",
        operation_type="build",
        accepted_task={"objective": "test"},
        accepted_plan={"plan_id": "plan.fixture"},
        authority_context={"authority": "host"},
        analysis_request=request,
        repository_context={"aflow_analysis_request": request, "scope": "scoped"},
        workspace_identity={"path": str(tmp_path / "workspace")},
        provider_policy={"provider": "local-openai-compatible", "model_id": "qwen2.5-coder:7b"},
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
    persist_audisor_stage(context, "analysis-package", {"package_sha256": package.package_hash, "package": package.canonical_payload})
    return request, package, context


def candidate(request: dict, decided_at: str | None = None, *, decision: str = "no_material_gap", findings=None) -> dict:
    return {
        "decision": decision,
        "blocking": decision != "no_material_gap",
        "execution_ready": decision == "no_material_gap",
        "findings": findings or [],
        "rejected_findings": [],
    }


def finding(request: dict) -> dict:
    evidence_id = request["evidence"][0]["evidence_id"]
    return {
        "schema_version": "1.0.0",
        "finding_id": "finding.missing-proof",
        "gap_type": "validation_evidence_gap",
        "origin": "semantic",
        "severity": "major",
        "blocking": True,
        "requirement_references": [request["success_definition"]["requirements"][0]["requirement_id"]],
        "plan_locations": ["/validations/0"],
        "specific_claim": "Required proof is absent.",
        "evidence_references": [{"evidence_id": evidence_id, "content_hash": request["evidence"][0]["content_hash"]}],
        "reasoning": "The package does not contain the required proof.",
        "why_it_matters": "The plan cannot be accepted without the proof.",
        "required_closure": {"closure_code": "add_sufficient_validation", "description": "Add the proof.", "acceptance_predicates": ["Proof exists."]},
        "status": "open",
    }


def invoke(worker: Worker, package_data):
    request, package, context = package_data
    decided_at = "2026-07-17T21:00:00Z"
    value = candidate(request, decided_at)
    worker.answer = json.dumps(value, separators=(",", ":"))
    result = invoke_audisor_analysis(
        {"id": "operation.build-001"},
        {"success_definition": {}, "execution_trajectory": [], "implementation_plan": [], "validation_contract": [], "fixture_specifications": [], "evidence_manifest": {}, "post_build_acceptance": {}},
        {"authority": {}, "baseline_evidence": {}, "accepted_constraints": [], "required_outputs": []},
        worker=worker,
        package=package,
        operation_context=context,
        decided_at=decided_at,
    )
    return result, context


def test_exact_authoritative_decision_is_accepted_and_compatibility_is_derived(tmp_path: Path) -> None:
    worker = Worker("")
    result, context = invoke(worker, prepared(tmp_path))
    assert worker.calls == 1
    assert result["frozen_aflow_result"]["material_gap_found"] is False
    assert result["frozen_aflow_result"]["evaluation_permitted"] is True
    assert result["frozen_aflow_result"]["correction_required"] is False
    assert result["frozen_aflow_result"]["unresolved_items"] == []
    assert (Path(context.workspace_identity["path"]) / "audisor-artifacts" / "raw-response.json").is_file()
    assert (Path(context.workspace_identity["path"]) / "audisor-artifacts" / "candidate-analysis.json").is_file()
    assert (Path(context.workspace_identity["path"]) / "audisor-artifacts" / "sealed-decision.json").is_file()
    assert (Path(context.workspace_identity["path"]) / "audisor-artifacts" / "adapter.json").is_file()


def test_candidate_schema_removes_exactly_host_owned_fields() -> None:
    from audisor.audisor_lifecycle.analysis_package import _registry

    documents, _registry_map = _registry()
    frozen = documents["analysis-decision.schema.json"]
    assert set(frozen["required"]) - set(_candidate_schema()["required"]) == HOST_OWNED_FIELDS
    assert set(frozen["properties"]) - set(_candidate_schema()["properties"]) == HOST_OWNED_FIELDS


@pytest.mark.parametrize("decision", ["material_gap_found", "missing_evidence", "contradicted", "drift_revalidation_required"])
def test_blocking_decisions_with_frozen_findings_are_accepted(tmp_path: Path, decision: str) -> None:
    request, package, context = prepared(tmp_path)
    worker = Worker(json.dumps(candidate(request, "2026-07-17T21:00:00Z", decision=decision, findings=[finding(request)])))
    result = invoke_audisor_analysis({}, {"success_definition": {}, "execution_trajectory": [], "implementation_plan": [], "validation_contract": [], "fixture_specifications": [], "evidence_manifest": {}, "post_build_acceptance": {}}, {"authority": {}, "baseline_evidence": {}, "accepted_constraints": [], "required_outputs": []}, worker=worker, package=package, operation_context=context, decided_at="2026-07-17T21:00:00Z")
    assert result["frozen_aflow_result"]["evaluation_permitted"] is False
    assert len(result["frozen_aflow_result"]["unresolved_items"]) == 1


@pytest.mark.parametrize("mutator,code", [
    (lambda value: value.update({"material_gap_found": False}), "forbidden_legacy_fields"),
    (lambda value: value.update({"unknown": True}), "candidate_schema_failed"),
    (lambda value: value.update({"analysis_id": "spoofed"}), "host_owned_field_in_candidate"),
    (lambda value: value.update({"findings": [finding(frozen_request())]}), "decision_inconsistent"),
])
def test_invalid_candidate_fails_closed(tmp_path: Path, mutator, code: str) -> None:
    request, package, context = prepared(tmp_path)
    value = candidate(request, "2026-07-17T21:00:00Z")
    mutator(value)
    worker = Worker(json.dumps(value))
    with pytest.raises(AudisorInvocationError) as caught:
        invoke_audisor_analysis({}, {}, {"authority": {}, "baseline_evidence": {}, "accepted_constraints": [], "required_outputs": []}, worker=worker, package=package, operation_context=context, decided_at="2026-07-17T21:00:00Z")
    assert caught.value.code == code
    assert caught.value.retry_prompt is not None
    assert "Original task and plan" in caught.value.retry_prompt
    assert "Required corrections" in caught.value.retry_prompt
    assert worker.calls == 1


@pytest.mark.parametrize("answer,code", [("prose {\"x\":1}", "invalid_json"), ("{} {}", "invalid_response_framing"), ("```json\\n{}\\n```", "invalid_response_framing")])
def test_response_framing_fails_closed(tmp_path: Path, answer: str, code: str) -> None:
    _request, package, context = prepared(tmp_path)
    worker = Worker(answer)
    with pytest.raises(AudisorInvocationError) as caught:
        invoke_audisor_analysis({}, {}, {"authority": {}, "baseline_evidence": {}, "accepted_constraints": [], "required_outputs": []}, worker=worker, package=package, operation_context=context, decided_at="2026-07-17T21:00:00Z")
    assert caught.value.code == code


def test_package_hash_mismatch_stops_before_model(tmp_path: Path) -> None:
    request, package, context = prepared(tmp_path)
    path = Path(context.workspace_identity["path"]) / "audisor-artifacts" / "analysis-package.json"
    value = json.loads(path.read_text())
    value["package_sha256"] = "sha256:" + "0" * 64
    path.write_text(json.dumps(value))
    worker = Worker(json.dumps(candidate(request, "2026-07-17T21:00:00Z")))
    with pytest.raises(AudisorInvocationError, match="hash"):
        invoke_audisor_analysis({}, {}, {}, worker=worker, package=package, operation_context=context, decided_at="2026-07-17T21:00:00Z")
    assert worker.calls == 0


def test_tool_call_and_multiple_choice_metadata_are_rejected(tmp_path: Path) -> None:
    request, package, context = prepared(tmp_path)
    worker = Worker(json.dumps(candidate(request, "2026-07-17T21:00:00Z")), tool=True)
    with pytest.raises(AudisorInvocationError) as caught:
        invoke_audisor_analysis({}, {}, {}, worker=worker, package=package, operation_context=context, decided_at="2026-07-17T21:00:00Z")
    assert caught.value.code == "tool_call_not_allowed"
