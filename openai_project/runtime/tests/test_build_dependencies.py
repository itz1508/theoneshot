"""Dependency graph validation and deterministic ordering."""

import pytest

from audisor.builder.dependencies import (
    DependencyValidationError,
    deterministic_topological_order,
)
from audisor.schemas.build import BuildTask


def task(task_id: str, depends_on: list[str] | None = None) -> BuildTask:
    return BuildTask(
        task_id=task_id,
        title=f"Task {task_id}",
        depends_on=depends_on or [],
        prompt="Structured later by coverage validation.",
        expected_outputs=[f"output/{task_id}.txt"],
        validation=[
            {
                "argv": ["python", "-m", "pytest"],
                "working_directory": ".",
                "acceptable_exit_codes": [0],
                "timeout_seconds": 60,
            }
        ],
    )


def test_deterministic_topological_order_is_independent_of_planner_order() -> None:
    first = [
        task("task-c", ["task-b", "task-a"]),
        task("task-b"),
        task("task-a"),
        task("task-d", ["task-a"]),
    ]
    second = list(reversed(first))

    first_order = deterministic_topological_order(first)
    second_order = deterministic_topological_order(second)

    assert [item.task_id for item in first_order] == [
        "task-a",
        "task-b",
        "task-c",
        "task-d",
    ]
    assert [item.task_id for item in second_order] == [
        "task-a",
        "task-b",
        "task-c",
        "task-d",
    ]
    assert first_order[2].depends_on == ["task-a", "task-b"]


def test_unknown_dependency_is_rejected() -> None:
    with pytest.raises(DependencyValidationError, match="unknown"):
        deterministic_topological_order([task("task-a", ["missing"])])


def test_self_dependency_is_rejected() -> None:
    with pytest.raises(DependencyValidationError, match="itself"):
        deterministic_topological_order([task("task-a", ["task-a"])])


def test_repeated_dependency_is_rejected() -> None:
    with pytest.raises(DependencyValidationError, match="repeated"):
        deterministic_topological_order(
            [task("task-a"), task("task-b", ["task-a", "task-a"])]
        )


def test_cycle_and_missing_root_are_rejected() -> None:
    with pytest.raises(DependencyValidationError, match="cycle"):
        deterministic_topological_order(
            [
                task("task-a", ["task-b"]),
                task("task-b", ["task-a"]),
            ]
        )


def test_duplicate_and_windows_case_colliding_ids_are_rejected() -> None:
    with pytest.raises(DependencyValidationError, match="unique"):
        deterministic_topological_order([task("task-a"), task("task-a")])
    with pytest.raises(DependencyValidationError, match="Windows"):
        deterministic_topological_order([task("Task-A"), task("task-a")])


def test_cycle_with_an_independent_root_is_rejected() -> None:
    with pytest.raises(DependencyValidationError, match="cycle"):
        deterministic_topological_order(
            [
                task("root"),
                task("task-a", ["task-b"]),
                task("task-b", ["task-a"]),
            ]
        )
