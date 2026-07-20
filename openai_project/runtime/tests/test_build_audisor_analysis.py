from __future__ import annotations

import json

import pytest

from audisor.audisor_lifecycle.build_analysis import BuildAnalysisError, evaluate_build
from audisor.audisor_lifecycle.ignition import ignite
from audisor.audisor_lifecycle.operation import FrozenAudisorPolicy
from audisor.schemas.task_output import TaskOutput


TASK = {"id": "task-1", "user_request": "add the feature"}
PLAN = {"plan_id": "plan-1", "steps": ["implement the feature"]}
CONTEXT = {"repository": "repo", "evidence": ["repo-state"]}


def response(*, gap: str = "no_material_gap") -> dict:
    findings = [] if gap == "no_material_gap" else [{
        "gap_id": "gap-1", "location": "plan.steps[0]", "claim": "the dependency is unspecified",
        "evidence": ["repo-state"], "correction": "name the dependency and validation",
    }]
    return {
        "gap_evaluation": {"result": gap, "findings": findings},
        "evaluation": {"result": "evaluated", "rationale": "The requested outcome is testable."},
        "success_definition": {"statement": "The requested feature is observable and validated.", "predicates": [{
            "predicate_id": "success-1", "observable_condition": "the feature behavior is present", "required_evidence": ["test_result"],
        }]},
        "validation": [{"validation_id": "validation-1", "command": ["pytest", "tests"], "pass_condition": "exit code is 0", "fail_condition": "exit code is nonzero"}],
        "fixtures": [{"fixture_id": "fixture-1", "input": {"task": "task-1"}, "expected": {"success": True}}],
        "updated_original_plan": dict(PLAN),
    }


class Worker:
    def __init__(self, value: dict):
        self.value = value
        self.calls = 0
        self.prompt = ""

    def execute(self, task):
        self.calls += 1
        self.prompt = task.prompt
        return TaskOutput(task_id=task.task_id, answer=json.dumps(self.value))


def test_build_aflow_calls_once_and_returns_analysis_only() -> None:
    worker = Worker(response())
    result = evaluate_build(task=TASK, original_plan=PLAN, repository_context=CONTEXT, worker=worker, operation_id="op-1")
    assert worker.calls == 1
    assert result.evaluation.result == "evaluated"
    assert result.updated_original_plan == PLAN
    assert not any(key in result.model_dump() for key in {"handoff", "execution_contract", "authority", "approved"})
    assert "original_plan" in worker.prompt


def test_material_gap_is_reported_without_build_authority() -> None:
    worker = Worker(response(gap="material_gap_found"))
    result = evaluate_build(task=TASK, original_plan=PLAN, repository_context=CONTEXT, worker=worker, operation_id="op-2")
    assert result.gap_evaluation.findings[0].correction


def test_unvalidated_success_fails_with_retry_prompt() -> None:
    value = response()
    value["evaluation"] = {"result": "not_evaluable", "rationale": "No observable evidence is supplied."}
    worker = Worker(value)
    with pytest.raises(BuildAnalysisError) as caught:
        evaluate_build(task=TASK, original_plan=PLAN, repository_context=CONTEXT, worker=worker, operation_id="op-3")
    assert caught.value.code == "success_undefined"
    assert "Revise the original implementation plan" in caught.value.retry_prompt


def test_build_ignite_does_not_assemble_contract() -> None:
    worker = Worker(response())
    result = ignite(
        policy=FrozenAudisorPolicy(True, "local-openai-compatible", "qwen2.5-coder:7b", "http://127.0.0.1:11434"),
        task_kind="build", task=TASK, repository_context=CONTEXT, supplied_plan=PLAN, worker=worker,
    )
    assert result.build_analysis is not None
    assert result.original_plan == PLAN
    assert result.updated_original_plan == PLAN
    assert result.candidate_plan is None
    assert result.execution_contract is None
    assert result.implementation_eligible is False
