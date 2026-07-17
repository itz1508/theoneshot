"""Strict worker-envelope and mutation-only action-plan rejection cases."""

import json

import pytest

from audisor.builder.action_parser import (
    ActionPlanError,
    normalize_worker_result,
    parse_action_plan,
)
from audisor.schemas.task_output import TaskOutput


def payload() -> dict:
    return {
        "summary": "Create greeting.py.",
        "mutations": [
            {
                "action_id": "mutation-001",
                "type": "write_file",
                "path": "src/greeting.py",
                "content": "def greet(name): return f'Hello, {name}'",
            }
        ],
        "expected_changed_paths": ["src/greeting.py"],
    }


def test_parser_accepts_json_only_mutation_plan_and_sanitizes_output() -> None:
    plan, sanitized = parse_action_plan(TaskOutput(task_id="task-001", answer=json.dumps(payload())))
    assert plan.summary == "Create greeting.py."
    assert [mutation.action_id for mutation in plan.mutations] == ["mutation-001"]
    assert sanitized.task_id == "task-001"
    assert len(sanitized.answer_sha256) == 64


def test_normalizer_accepts_only_complete_typed_provider_envelopes() -> None:
    raw = json.dumps(payload())
    expected = TaskOutput(task_id="task-001", answer=raw)
    assert normalize_worker_result(expected, "task-001") == expected
    with pytest.raises(ActionPlanError, match="unusable"):
        normalize_worker_result(raw, "task-001")
    with pytest.raises(ActionPlanError, match="unusable"):
        normalize_worker_result({"task_id": "task-001", "answer": raw}, "task-001")
    with pytest.raises(ActionPlanError, match="mismatched"):
        normalize_worker_result(TaskOutput(task_id="other", answer=raw), "task-001")


@pytest.mark.parametrize(
    "answer",
    [
        "not json",
        "```json\n{}\n```",
        json.dumps(payload()) + " trailing",
        "[]",
        '{"summary":"x","summary":"y","mutations":[],"expected_changed_paths":[]}',
        '{"summary":NaN,"mutations":[],"expected_changed_paths":[]}',
    ],
)
def test_parser_rejects_malformed_fenced_duplicate_or_nonfinite_json(answer: str) -> None:
    with pytest.raises(ActionPlanError):
        parse_action_plan(TaskOutput(task_id="task-001", answer=answer))


def test_parser_rejects_run_command_and_legacy_actions_array() -> None:
    run_command = payload()
    run_command["mutations"] = [
        {"action_id": "command-001", "type": "run_command", "argv": ["python", "-V"]}
    ]
    with pytest.raises(ActionPlanError):
        parse_action_plan(TaskOutput(task_id="task-001", answer=json.dumps(run_command)))

    legacy = payload()
    legacy["actions"] = legacy.pop("mutations")
    with pytest.raises(ActionPlanError):
        parse_action_plan(TaskOutput(task_id="task-001", answer=json.dumps(legacy)))


def test_parser_rejects_unknown_mutation_or_extra_field_before_execution() -> None:
    unknown = payload()
    unknown["mutations"][0]["type"] = "shell"
    with pytest.raises(ActionPlanError):
        parse_action_plan(TaskOutput(task_id="task-001", answer=json.dumps(unknown)))
    extra = payload()
    extra["mutations"][0]["unexpected"] = True
    with pytest.raises(ActionPlanError):
        parse_action_plan(TaskOutput(task_id="task-001", answer=json.dumps(extra)))


def test_parser_rejects_duplicate_mutation_ids() -> None:
    invalid = payload()
    invalid["mutations"].append(
        {"action_id": "MUTATION-001", "type": "create_directory", "path": "src/other"}
    )
    with pytest.raises(ActionPlanError):
        parse_action_plan(TaskOutput(task_id="task-001", answer=json.dumps(invalid)))


@pytest.mark.parametrize("path", ["../outside.txt", r"C:\outside.txt", r"\\server\share\x", "src:file", "NUL/file"])
def test_parser_rejects_absolute_traversal_unc_ads_and_reserved_paths(path: str) -> None:
    invalid = payload()
    invalid["mutations"][0]["path"] = path
    with pytest.raises(ActionPlanError):
        parse_action_plan(TaskOutput(task_id="task-001", answer=json.dumps(invalid)))


def test_parser_rejects_environment_secret_values(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "sentinel-secret-value-12345"
    monkeypatch.setenv("PHASE2B_TEST_API_KEY", secret)
    invalid = payload()
    invalid["mutations"][0]["content"] = secret
    with pytest.raises(ActionPlanError, match="protected"):
        parse_action_plan(TaskOutput(task_id="task-001", answer=json.dumps(invalid)))
