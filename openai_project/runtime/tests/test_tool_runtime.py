"""Mutation-only workspace effects; command execution belongs to SandboxRunner."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from audisor.builder.tool_runtime import ToolRuntime, ToolRuntimeError
from audisor.schemas.execution import WorkerActionPlan


def runtime(tmp_path: Path) -> tuple[ToolRuntime, Path]:
    workspace = tmp_path / "execution" / "workspace"
    (workspace / "src").mkdir(parents=True)
    (workspace / "tests").mkdir()
    return ToolRuntime(workspace, ("src", "tests"), tmp_path / "execution" / "command-temp"), workspace


def progress(*_args) -> None:
    return None


def plan(mutations: list[dict], expected: list[str]) -> WorkerActionPlan:
    return WorkerActionPlan.model_validate(
        {"summary": "Apply approved mutations.", "mutations": mutations, "expected_changed_paths": expected}
    )


def test_runtime_applies_mutations_and_records_exact_changed_paths(tmp_path: Path) -> None:
    tools, workspace = runtime(tmp_path)
    actions, commands, changes = tools.execute(
        plan(
            [{"action_id": "write-001", "type": "write_file", "path": "src/greeting.py", "content": "def greet(): return 'Hello'\n"}],
            ["src/greeting.py"],
        ),
        progress,
    )
    assert (workspace / "src/greeting.py").is_file()
    assert [action.status for action in actions] == ["completed"]
    assert commands == []
    assert [(item.path, item.change) for item in changes] == [("src/greeting.py", "created")]


def test_runtime_rejects_unapproved_write_path_without_creating_it(tmp_path: Path) -> None:
    tools, workspace = runtime(tmp_path)
    with pytest.raises(ToolRuntimeError, match="allowed"):
        tools.execute(plan([{"action_id": "write-001", "type": "write_file", "path": "outside.txt", "content": "no"}], ["outside.txt"]), progress)
    assert not (workspace / "outside.txt").exists()


def test_runtime_rejects_symlink_escape_when_supported(tmp_path: Path) -> None:
    tools, workspace = runtime(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    link = workspace / "src" / "link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is unavailable")
    with pytest.raises(ToolRuntimeError, match="reparse"):
        tools.execute(plan([{"action_id": "write-001", "type": "write_file", "path": "src/link/outside.txt", "content": "no"}], ["src/link/outside.txt"]), progress)
    assert not (outside / "outside.txt").exists()


def test_plan_schema_rejects_command_actions_before_runtime() -> None:
    with pytest.raises(ValidationError):
        plan([{"action_id": "command-001", "type": "run_command", "argv": ["python", "-V"]}], [])


def test_runtime_rejects_expected_change_mismatch(tmp_path: Path) -> None:
    tools, _workspace = runtime(tmp_path)
    with pytest.raises(ToolRuntimeError, match="expected_changed_paths"):
        tools.execute(plan([{"action_id": "write-001", "type": "write_file", "path": "src/greeting.py", "content": "x"}], ["src/other.py"]), progress)
