"""Planning-worker parsing, validation, ordering, and persistence."""

import json
from pathlib import Path
from typing import Any

import pytest

from audisor.builder.preparer import BuildPreparer
from audisor.builder.store import BuildAlreadyExistsError, BuildStore
from provider_testkit import provider_router
from audisor.schemas.build import BuildRequest
from audisor.workers.base import ProviderError, ProviderInvalidResponseError


class FakeWorker:
    name = "fake-planner"

    def __init__(self, result: object) -> None:
        self.result = result
        self.calls = []

    def execute(self, task) -> object:
        self.calls.append(task)
        return self.result


class NeverWorker:
    name = "never"

    def execute(self, task) -> object:
        raise AssertionError("unselected worker was called")


def complete_prompt(label: str) -> str:
    return f"""## Objective
Complete {label}.

## Inputs and repository paths
Use the current repository root and the paths in the build instruction.

## Required work
Implement {label} completely.

## Ordered steps
1. Inspect the active implementation.
2. Implement and validate {label}.

## Expected output
Return the completed {label}.

## Validation
Run focused tests for {label}.

## Evidence to return
Return changed files and test output."""


def ready_payload(build_id: str = "build-001") -> dict[str, Any]:
    return {
        "build_id": build_id,
        "status": "ready",
        "gaps": [],
        "tasks": [
            {
                "task_id": "task-002",
                "title": "Add tests",
                "depends_on": ["task-001"],
                "prompt": complete_prompt("tests"),
                "expected_outputs": ["tests/test_module.py"],
                "validation": [{"argv": ["python", "-m", "pytest", "tests"], "working_directory": ".", "acceptable_exit_codes": [0], "timeout_seconds": 60}],
            },
            {
                "task_id": "task-001",
                "title": "Create module",
                "depends_on": [],
                "prompt": complete_prompt("module"),
                "expected_outputs": ["src/module.py"],
                "validation": [{"argv": ["python", "-m", "pytest", "tests"], "working_directory": ".", "acceptable_exit_codes": [0], "timeout_seconds": 60}],
            },
        ],
    }


def preparer_for(result: object, data_dir: Path) -> tuple[BuildPreparer, FakeWorker]:
    worker = FakeWorker(result)
    router = provider_router("fireworks", worker, NeverWorker())
    return BuildPreparer(router, BuildStore(data_dir)), worker


def test_valid_preparation_orders_tasks_builds_prompt_and_persists(tmp_path: Path) -> None:
    preparer, worker = preparer_for(json.dumps(ready_payload()), tmp_path / "data")
    request = BuildRequest(
        build_id="build-001",
        instruction="Create a module and tests.",
    )
    plan = preparer.prepare(request)

    assert [task.task_id for task in plan.tasks] == ["task-001", "task-002"]
    assert worker.calls and "Create a module and tests." in worker.calls[0].prompt
    assert "Return JSON only" in worker.calls[0].prompt
    final = tmp_path / "data" / "builds" / "build-001"
    assert (final / "instruction.json").is_file()
    assert (final / "plan.json").is_file()
    assert len(list((final / "skills").glob("*/SKILL.md"))) == 2


def test_blocked_preparation_returns_and_persists_without_skills(tmp_path: Path) -> None:
    result = json.dumps(
        {
            "build_id": "build-001",
            "status": "blocked",
            "gaps": ["The target repository path is missing."],
            "tasks": [],
        }
    )
    preparer, _worker = preparer_for(result, tmp_path / "data")
    plan = preparer.prepare(
        BuildRequest(build_id="build-001", instruction="Prepare the build.")
    )

    assert plan.status == "blocked"
    assert plan.tasks == []
    final = tmp_path / "data" / "builds" / "build-001"
    assert list((final / "skills").iterdir()) == []


@pytest.mark.parametrize(
    "result",
    [
        "not JSON",
        "~~~json\n{}\n~~~",
        '{"build_id":"build-001","build_id":"other"}',
        '{"build_id":"build-001","status":NaN,"gaps":[],"tasks":[]}',
        "[]",
        123,
        json.dumps({**ready_payload(), "unexpected": True}),
        json.dumps({**ready_payload(), "status": "other"}),
    ],
)
def test_malformed_strict_or_invariant_breaking_planner_output_leaves_no_build(
    tmp_path: Path,
    result: object,
) -> None:
    preparer, _worker = preparer_for(result, tmp_path / "data")
    with pytest.raises(ProviderError):
        preparer.prepare(
            BuildRequest(build_id="build-001", instruction="Prepare the build.")
        )
    assert not (tmp_path / "data" / "builds" / "build-001").exists()


@pytest.mark.parametrize(
    "tasks",
    [
        [
            {
                "task_id": "task-001",
                "title": "Unknown dependency",
                "depends_on": ["missing"],
                "prompt": complete_prompt("task"),
                "expected_outputs": ["src/task.py"],
                "validation": [{"argv": ["python", "-m", "pytest"], "working_directory": ".", "acceptable_exit_codes": [0], "timeout_seconds": 60}],
            }
        ],
        [
            {
                "task_id": "task-001",
                "title": "Self dependency",
                "depends_on": ["task-001"],
                "prompt": complete_prompt("task"),
                "expected_outputs": ["src/task.py"],
                "validation": [{"argv": ["python", "-m", "pytest"], "working_directory": ".", "acceptable_exit_codes": [0], "timeout_seconds": 60}],
            }
        ],
        [
            {
                "task_id": "task-001",
                "title": "Cycle one",
                "depends_on": ["task-002"],
                "prompt": complete_prompt("task one"),
                "expected_outputs": ["src/task-one.py"],
                "validation": [{"argv": ["python", "-m", "pytest"], "working_directory": ".", "acceptable_exit_codes": [0], "timeout_seconds": 60}],
            },
            {
                "task_id": "task-002",
                "title": "Cycle two",
                "depends_on": ["task-001"],
                "prompt": complete_prompt("task two"),
                "expected_outputs": ["src/task-two.py"],
                "validation": [{"argv": ["python", "-m", "pytest"], "working_directory": ".", "acceptable_exit_codes": [0], "timeout_seconds": 60}],
            },
        ],
    ],
)
def test_invalid_dependencies_are_rejected_before_storage(
    tmp_path: Path,
    tasks: list[dict],
) -> None:
    payload = {
        "build_id": "build-001",
        "status": "ready",
        "gaps": [],
        "tasks": tasks,
    }
    preparer, _worker = preparer_for(json.dumps(payload), tmp_path / "data")
    with pytest.raises(ProviderInvalidResponseError) as captured:
        preparer.prepare(
            BuildRequest(build_id="build-001", instruction="Prepare the build.")
        )
    assert captured.value.internal_detail == "dependencies=invalid"
    assert not (tmp_path / "data" / "builds" / "build-001").exists()


@pytest.mark.parametrize(
    "title,prompt",
    [
        ("TBD", complete_prompt("task")),
        ("Complete task", "## Objective\nTBD"),
        (
            "Complete task",
            complete_prompt("task").replace(
                "## Validation\nRun focused tests for task.",
                "## Validation\nTBD",
            ),
        ),
    ],
)
def test_placeholder_or_incomplete_tasks_are_rejected_before_storage(
    tmp_path: Path,
    title: str,
    prompt: str,
) -> None:
    payload = {
        "build_id": "build-001",
        "status": "ready",
        "gaps": [],
        "tasks": [
            {
                "task_id": "task-001",
                "title": title,
                "depends_on": [],
                "prompt": prompt,
                "expected_outputs": ["src/task.py"],
                "validation": [{"argv": ["python", "-m", "pytest"], "working_directory": ".", "acceptable_exit_codes": [0], "timeout_seconds": 60}],
            }
        ],
    }
    preparer, _worker = preparer_for(json.dumps(payload), tmp_path / "data")
    with pytest.raises(ProviderError):
        preparer.prepare(
            BuildRequest(build_id="build-001", instruction="Prepare the build.")
        )
    assert not (tmp_path / "data" / "builds" / "build-001").exists()


def test_mismatched_build_id_is_rejected_before_storage(tmp_path: Path) -> None:
    preparer, _worker = preparer_for(
        json.dumps(ready_payload("other-build")),
        tmp_path / "data",
    )
    with pytest.raises(ProviderInvalidResponseError) as captured:
        preparer.prepare(
            BuildRequest(build_id="build-001", instruction="Prepare the build.")
        )
    assert captured.value.internal_detail == "build_id=mismatch"
    assert not (tmp_path / "data" / "builds" / "build-001").exists()


def test_existing_build_is_rejected_before_second_worker_call(tmp_path: Path) -> None:
    store = BuildStore(tmp_path / "data")
    first_worker = FakeWorker(json.dumps(ready_payload()))
    BuildPreparer(
        provider_router("fireworks", first_worker, NeverWorker()),
        store,
    ).prepare(BuildRequest(build_id="build-001", instruction="First."))

    second_worker = FakeWorker(json.dumps(ready_payload()))
    second = BuildPreparer(
        provider_router("fireworks", second_worker, NeverWorker()),
        store,
    )
    with pytest.raises(BuildAlreadyExistsError):
        second.prepare(BuildRequest(build_id="build-001", instruction="Second."))
    assert second_worker.calls == []
