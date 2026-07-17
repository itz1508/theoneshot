"""Published JSON Schema and Pydantic structural parity corpus."""

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from audisor.schemas.build import BuildPlan
from audisor.schemas.execution import (
    BuildExecutionRequest,
    BuildExecutionState,
    TaskExecutionResult,
    WorkerActionPlan,
)

SCHEMAS = Path(__file__).resolve().parents[2] / "schemas"


def schema(name: str) -> Draft202012Validator:
    payload = json.loads((SCHEMAS / name).read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(payload)
    return Draft202012Validator(payload)


def ready_task() -> dict[str, object]:
    return {
        "task_id": "task-001",
        "title": "Create greeting",
        "depends_on": [],
        "prompt": "## Objective\nDo work.\n## Inputs and repository paths\nUse repo.\n## Required work\nWrite.\n## Ordered steps\n1. Write.\n## Expected output\nFile.\n## Validation\nValidate.\n## Evidence to return\nHash.",
        "expected_outputs": ["src/greeting.py"],
        "validation": [{"argv": ["python", "-V"], "working_directory": ".", "acceptable_exit_codes": [0], "timeout_seconds": 30}],
    }


def mutation_only_result() -> dict[str, object]:
    return {
        "build_id": "build-001",
        "execution_id": "execution-001",
        "task_id": "task-001",
        "status": "completed",
        "skill_hash": "a" * 64,
        "plan_hash": "b" * 64,
        "worker_input": {"task_id": "task-001", "prompt": "work"},
        "worker_dispatched": True,
        "worker_output": None,
        "requested_actions": None,
        "executed_actions": [],
        "changed_paths": [],
        "validation_commands": [],
        "exit_codes": [],
        "prepared_validation_sha256": "c" * 64,
        "rendered_validation_sha256": "c" * 64,
        "executed_validation_sha256": None,
        "expected_outputs_verified": True,
        "completion_timestamp": "2026-07-16T00:00:00Z",
        "error": None,
    }


def rejects_both(model, validator: Draft202012Validator, payload: object) -> None:
    with pytest.raises(ValidationError):
        model.model_validate(payload)
    assert list(validator.iter_errors(payload))


def test_plan_missing_validation_rejected_by_both() -> None:
    task = ready_task()
    task.pop("validation")
    rejects_both(
        BuildPlan,
        schema("build-plan.schema.json"),
        {"build_id": "build-001", "status": "ready", "gaps": [], "tasks": [task]},
    )


def test_plan_missing_expected_outputs_rejected_by_both() -> None:
    task = ready_task()
    task.pop("expected_outputs")
    rejects_both(
        BuildPlan,
        schema("build-plan.schema.json"),
        {"build_id": "build-001", "status": "ready", "gaps": [], "tasks": [task]},
    )


def test_mutation_only_result_accepts_deferred_executable_validation_in_both() -> None:
    payload = mutation_only_result()
    TaskExecutionResult.model_validate(payload)
    schema("task-execution-result.schema.json").validate(payload)


def test_task_result_rejects_executed_validation_in_both() -> None:
    payload = mutation_only_result()
    payload["executed_validation_sha256"] = "d" * 64
    rejects_both(
        TaskExecutionResult,
        schema("task-execution-result.schema.json"),
        payload,
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"summary": "bad", "mutations": [{"action_id": "m1", "type": "write_file", "path": "../escape.py", "content": "x"}], "expected_changed_paths": ["../escape.py"]},
        {"summary": "bad", "mutations": [{"action_id": "m1", "type": "run_command", "argv": ["python", "x.py"]}], "expected_changed_paths": ["src/x.py"]},
    ],
)
def test_unsafe_or_unknown_mutation_rejected_by_both(payload: object) -> None:
    rejects_both(WorkerActionPlan, schema("worker-action-plan.schema.json"), payload)


@pytest.mark.parametrize("action_id", ["CON", "nul.txt", "COM1"])
def test_reserved_mutation_action_id_rejected_by_both(action_id: str) -> None:
    payload = {
        "summary": "bad",
        "mutations": [
            {
                "action_id": action_id,
                "type": "write_file",
                "path": "src/example.py",
                "content": "x",
            }
        ],
        "expected_changed_paths": ["src/example.py"],
    }
    rejects_both(WorkerActionPlan, schema("worker-action-plan.schema.json"), payload)


@pytest.mark.parametrize("execution_id", ["../escape", "CON", "prn.txt", "LPT9"])
def test_unsafe_execution_id_rejected_by_both(execution_id: str) -> None:
    payload = {"execution_id": execution_id, "idempotency_key": "key-001", "target_root": "D:/tmp/target", "allowed_write_paths": ["src"]}
    rejects_both(BuildExecutionRequest, schema("build-execution-input.schema.json"), payload)


def test_contradictory_terminal_state_rejected_by_both() -> None:
    payload = {
        "build_id": "build-001",
        "execution_id": "execution-001",
        "status": "completed",
        "tasks": [{"task_id": "task-001", "status": "failed"}],
        "terminal_manifest_sha256": "a" * 64,
    }
    rejects_both(BuildExecutionState, schema("build-execution-state.schema.json"), payload)


def test_exact_duplicate_execution_task_record_rejected_by_both() -> None:
    task = {"task_id": "task-001", "status": "running"}
    payload = {
        "build_id": "build-001",
        "execution_id": "execution-001",
        "status": "running",
        "tasks": [task, task],
    }
    rejects_both(BuildExecutionState, schema("build-execution-state.schema.json"), payload)


def test_runtime_rejects_semantic_duplicate_task_id_beyond_json_schema_unique_items() -> None:
    payload = {
        "build_id": "build-001",
        "execution_id": "execution-001",
        "status": "running",
        "tasks": [
            {"task_id": "task-001", "status": "running"},
            {"task_id": "task-001", "status": "pending"},
        ],
    }
    with pytest.raises(ValidationError):
        BuildExecutionState.model_validate(payload)
    published = json.loads((SCHEMAS / "build-execution-state.schema.json").read_text(encoding="utf-8"))
    assert published["properties"]["tasks"]["x-unique-by"] == "task_id"
    assert not list(Draft202012Validator(published).iter_errors(payload))


@pytest.mark.parametrize("field", ["build_id", "execution_id"])
def test_unsafe_execution_state_identifier_rejected_by_both(field: str) -> None:
    payload = {
        "build_id": "build-001",
        "execution_id": "execution-001",
        "status": "running",
        "tasks": [{"task_id": "task-001", "status": "running"}],
    }
    payload[field] = "../escape"
    rejects_both(BuildExecutionState, schema("build-execution-state.schema.json"), payload)


@pytest.mark.parametrize(
    ("field", "reserved"),
    [("build_id", "CON"), ("execution_id", "nul.txt"), ("task_id", "COM1")],
)
def test_reserved_execution_state_identifier_rejected_by_both(
    field: str, reserved: str
) -> None:
    payload = {
        "build_id": "build-001",
        "execution_id": "execution-001",
        "status": "running",
        "tasks": [{"task_id": "task-001", "status": "running"}],
    }
    if field == "task_id":
        payload["tasks"][0]["task_id"] = reserved
    else:
        payload[field] = reserved
    rejects_both(BuildExecutionState, schema("build-execution-state.schema.json"), payload)


def test_contradictory_interrupted_state_rejected_by_both() -> None:
    payload = {
        "build_id": "build-001",
        "execution_id": "execution-001",
        "status": "interrupted",
        "tasks": [{"task_id": "task-001", "status": "running"}],
    }
    rejects_both(BuildExecutionState, schema("build-execution-state.schema.json"), payload)
