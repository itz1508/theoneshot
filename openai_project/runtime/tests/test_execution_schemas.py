"""Phase 2B mutation-only request, state, and worker-plan schema probes."""

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from audisor.schemas.execution import BuildExecutionRequest, BuildExecutionState, WorkerActionPlan

SCHEMAS = Path(__file__).resolve().parents[2] / "schemas"
MANIFEST_HASH = "a" * 64


def valid_request() -> dict:
    return {"execution_id": "execution-001", "idempotency_key": "execution-001-request", "target_root": r"D:\Dev\target-project", "allowed_write_paths": ["src", "tests"]}


def valid_mutation_plan() -> dict:
    return {
        "summary": "Create the greeting module.",
        "mutations": [{"action_id": "mutation-001", "type": "write_file", "path": "src/greeting.py", "content": "def greet(name): return f'Hello, {name}'"}],
        "expected_changed_paths": ["src/greeting.py"],
    }


def test_build_execution_request_accepts_explicit_authority() -> None:
    request = BuildExecutionRequest.model_validate(valid_request())
    assert request.execution_id == "execution-001"
    assert request.allowed_write_paths == ["src", "tests"]


@pytest.mark.parametrize("field,value", [("execution_id", "../escape"), ("execution_id", "CON"), ("idempotency_key", ""), ("target_root", ""), ("target_root", "\x00bad")])
def test_execution_request_rejects_missing_or_unsafe_fields(field: str, value: object) -> None:
    payload = valid_request()
    payload[field] = value
    with pytest.raises(ValidationError):
        BuildExecutionRequest.model_validate(payload)


@pytest.mark.parametrize("path", ["../outside", r"..\outside", r"C:\outside", r"\\server\share", "src:stream", "src.", "NUL/file"])
def test_execution_request_rejects_unsafe_allowed_paths(path: str) -> None:
    payload = valid_request()
    payload["allowed_write_paths"] = [path]
    with pytest.raises(ValidationError):
        BuildExecutionRequest.model_validate(payload)


def test_mutation_plan_is_strict_and_rejects_legacy_or_command_concepts() -> None:
    plan = WorkerActionPlan.model_validate(valid_mutation_plan())
    assert [mutation.type for mutation in plan.mutations] == ["write_file"]
    for field, value in (
        ("actions", valid_mutation_plan()["mutations"]),
        ("mutations", [{"action_id": "command-001", "type": "run_command", "argv": ["python", "-V"]}]),
        ("validation", [{"argv": ["python", "-V"]}]),
    ):
        invalid = valid_mutation_plan()
        invalid[field] = value
        with pytest.raises(ValidationError):
            WorkerActionPlan.model_validate(invalid)


@pytest.mark.parametrize(
    "state",
    [
        {"status": "completed", "tasks": [{"task_id": "task-001", "status": "pending"}]},
        {"status": "completed", "tasks": [{"task_id": "task-001", "status": "completed"}]},
        {"status": "failed", "tasks": [{"task_id": "task-001", "status": "completed"}]},
        {"status": "failed", "tasks": [{"task_id": "task-001", "status": "failed"}, {"task_id": "TASK-001", "status": "blocked"}]},
    ],
)
def test_execution_state_rejects_contradictory_or_unmanifested_terminal_states(state: dict) -> None:
    payload = {"build_id": "build-001", "execution_id": "execution-001", **state}
    with pytest.raises(ValidationError):
        BuildExecutionState.model_validate(payload)


def test_execution_state_accepts_manifest_anchored_terminal_state() -> None:
    state = BuildExecutionState.model_validate(
        {"build_id": "build-001", "execution_id": "execution-001", "status": "failed", "terminal_manifest_sha256": MANIFEST_HASH, "tasks": [{"task_id": "task-001", "status": "failed"}, {"task_id": "task-002", "status": "blocked"}]}
    )
    assert state.status == "failed"


def test_published_input_state_and_action_schemas_reject_unsafe_examples() -> None:
    cases = (
        ("build-execution-input.schema.json", valid_request(), {**valid_request(), "allowed_write_paths": ["../escape"]}),
        ("worker-action-plan.schema.json", valid_mutation_plan(), {**valid_mutation_plan(), "mutations": [{"action_id": "command-001", "type": "run_command", "argv": ["python", "-V"]}]}),
    )
    for filename, valid, invalid in cases:
        schema = json.loads((SCHEMAS / filename).read_text(encoding="utf-8"))
        validator = Draft202012Validator(schema)
        validator.validate(valid)
        assert list(validator.iter_errors(invalid)), filename


def test_all_phase2b_json_schemas_are_draft_2020_12_valid() -> None:
    for filename in ("build-execution-input.schema.json", "build-execution-state.schema.json", "task-execution-result.schema.json", "worker-action-plan.schema.json", "execution-evidence.schema.json"):
        Draft202012Validator.check_schema(json.loads((SCHEMAS / filename).read_text(encoding="utf-8")))
