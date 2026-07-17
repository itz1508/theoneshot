"""Deterministic sequential dependency-state behavior."""

import pytest

from audisor.builder.scheduler import DeterministicScheduler, SchedulerError
from audisor.schemas.build import BuildPlan
from audisor.schemas.execution import BuildExecutionState, TaskStateRecord


def task(
    task_id: str,
    title: str,
    depends_on: list[str],
    output: str,
    prompt: str,
) -> dict[str, object]:
    return {
        "task_id": task_id,
        "title": title,
        "depends_on": depends_on,
        "prompt": prompt,
        "expected_outputs": [output],
        "validation": [
            {
                "argv": ["python", "-c", "raise SystemExit(0)"],
                "working_directory": ".",
                "acceptable_exit_codes": [0],
                "timeout_seconds": 30,
            }
        ],
    }


def plan() -> BuildPlan:
    prompt = """## Objective
Do work.
## Inputs and repository paths
Use the fixture.
## Required work
Create files.
## Ordered steps
1. Work.
## Expected output
Files.
## Validation
Run tests.
## Evidence to return
Return evidence."""
    return BuildPlan.model_validate(
        {
            "build_id": "build-001",
            "status": "ready",
            "gaps": [],
            "tasks": [
                task("task-001", "One", [], "src/one.py", prompt),
                task("task-002", "Two", ["task-001"], "src/two.py", prompt),
                task("task-003", "Three", ["task-002"], "src/three.py", prompt),
            ],
        }
    )


def independent_branch_plan() -> BuildPlan:
    base = plan().tasks[0].prompt
    return BuildPlan.model_validate(
        {
            "build_id": "build-001",
            "status": "ready",
            "gaps": [],
            "tasks": [
                task("task-001", "Failing root", [], "src/root.py", base),
                task(
                    "task-002",
                    "Dependent",
                    ["task-001"],
                    "src/dependent.py",
                    base,
                ),
                task("task-003", "Independent", [], "docs/usage.md", base),
            ],
        }
    )


def test_only_root_is_initially_ready() -> None:
    scheduler = DeterministicScheduler(plan(), "execution-001")
    state = scheduler.initial_state()
    assert [item.status for item in state.tasks] == ["ready", "pending", "pending"]
    assert scheduler.next_ready(state).task_id == "task-001"


def test_dependents_release_only_after_prerequisite_completion() -> None:
    scheduler = DeterministicScheduler(plan(), "execution-001")
    state = scheduler.mark_running(scheduler.initial_state(), "task-001")
    assert scheduler.next_ready(state) is None
    state = scheduler.mark_completed(state, "task-001")
    assert scheduler.next_ready(state).task_id == "task-002"
    assert [item.status for item in state.tasks] == ["completed", "ready", "pending"]


def test_scheduler_preserves_plan_order_and_completes_deterministically() -> None:
    scheduler = DeterministicScheduler(plan(), "execution-001")
    state = scheduler.initial_state()
    executed = []
    while True:
        task = scheduler.next_ready(state)
        if task is None:
            break
        executed.append(task.task_id)
        state = scheduler.mark_completed(scheduler.mark_running(state, task.task_id), task.task_id)
    assert executed == ["task-001", "task-002", "task-003"]
    assert state.status == "running"
    assert scheduler.terminal_status(state) == "completed"


def test_each_task_can_start_at_most_once() -> None:
    scheduler = DeterministicScheduler(plan(), "execution-001")
    state = scheduler.mark_running(scheduler.initial_state(), "task-001")
    with pytest.raises(SchedulerError):
        scheduler.mark_running(state, "task-001")


def test_failure_blocks_direct_and_indirect_dependents() -> None:
    scheduler = DeterministicScheduler(plan(), "execution-001")
    state = scheduler.mark_running(scheduler.initial_state(), "task-001")
    state = scheduler.mark_failed(state, "task-001")
    assert state.status == "running"
    assert [item.status for item in state.tasks] == ["failed", "blocked", "blocked"]
    assert scheduler.next_ready(state) is None
    assert scheduler.terminal_status(state) == "failed"


def test_failure_blocks_dependents_but_continues_independent_ready_branch() -> None:
    scheduler = DeterministicScheduler(independent_branch_plan(), "execution-001")
    state = scheduler.mark_running(scheduler.initial_state(), "task-001")
    state = scheduler.mark_failed(state, "task-001")

    assert [item.status for item in state.tasks] == ["failed", "blocked", "ready"]
    assert scheduler.next_ready(state).task_id == "task-003"
    with pytest.raises(SchedulerError, match="non-terminal"):
        scheduler.terminal_status(state)

    state = scheduler.mark_running(state, "task-003")
    state = scheduler.mark_completed(state, "task-003")

    assert [item.status for item in state.tasks] == ["failed", "blocked", "completed"]
    assert scheduler.next_ready(state) is None
    assert scheduler.terminal_status(state) == "failed"


def test_stale_running_becomes_interrupted_without_changing_completed_tasks() -> None:
    scheduler = DeterministicScheduler(plan(), "execution-001")
    state = BuildExecutionState.model_validate(
        {
            "build_id": "build-001",
            "execution_id": "execution-001",
            "status": "running",
            "tasks": [
                {"task_id": "task-001", "status": "completed"},
                {"task_id": "task-002", "status": "running"},
                {"task_id": "task-003", "status": "pending"},
            ],
        }
    )
    interrupted = scheduler.interrupt_running(state)
    assert interrupted.status == "interrupted"
    assert [item.status for item in interrupted.tasks] == [
        "completed",
        "interrupted",
        "interrupted",
    ]


def test_state_validation_rejects_missing_unknown_and_case_duplicate_task_ids() -> None:
    scheduler = DeterministicScheduler(plan(), "execution-001")
    missing = scheduler.initial_state().model_copy(
        update={"tasks": scheduler.initial_state().tasks[:2]}
    )
    unknown = scheduler.initial_state().model_copy(
        update={
            "tasks": [
                *scheduler.initial_state().tasks,
                TaskStateRecord(task_id="task-999", status="pending"),
            ]
        }
    )
    case_duplicate = BuildExecutionState.model_construct(
        build_id="build-001",
        execution_id="execution-001",
        status="running",
        tasks=[
            *scheduler.initial_state().tasks,
            TaskStateRecord(task_id="TASK-001", status="pending"),
        ],
        terminal_manifest_sha256=None,
    )

    for invalid in (missing, unknown, case_duplicate):
        with pytest.raises(SchedulerError, match="tasks|duplicate"):
            scheduler.validate_state(invalid)


def test_state_validation_rejects_completed_task_with_failed_dependency() -> None:
    scheduler = DeterministicScheduler(independent_branch_plan(), "execution-001")
    invalid = BuildExecutionState.model_validate(
        {
            "build_id": "build-001",
            "execution_id": "execution-001",
            "status": "failed",
            "terminal_manifest_sha256": "a" * 64,
            "tasks": [
                {"task_id": "task-001", "status": "failed"},
                {"task_id": "task-002", "status": "completed"},
                {"task_id": "task-003", "status": "completed"},
            ],
        }
    )

    with pytest.raises(SchedulerError, match="incomplete dependency"):
        scheduler.validate_state(invalid)
