"""Sequential deterministic state transitions for a prepared task graph."""

from __future__ import annotations

from audisor.schemas.build import BuildPlan, BuildTask
from audisor.schemas.execution import (
    BuildExecutionState,
    TaskStateRecord,
)


class SchedulerError(RuntimeError):
    """An invalid or duplicate task transition was requested."""


class DeterministicScheduler:
    """Release exactly one dependency-ready task in prepared-plan order."""

    def __init__(self, plan: BuildPlan, execution_id: str) -> None:
        self.plan = plan
        self.tasks = {task.task_id: task for task in plan.tasks}
        self.execution_id = execution_id

    def initial_state(self) -> BuildExecutionState:
        return BuildExecutionState(
            build_id=self.plan.build_id,
            execution_id=self.execution_id,
            status="running",
            tasks=[
                TaskStateRecord(
                    task_id=task.task_id,
                    status="ready" if not task.depends_on else "pending",
                )
                for task in self.plan.tasks
            ],
        )

    @staticmethod
    def _status_map(state: BuildExecutionState) -> dict[str, TaskStateRecord]:
        return {item.task_id: item for item in state.tasks}

    def validate_state(self, state: BuildExecutionState) -> None:
        """Reject state that does not exactly represent the prepared task graph."""
        if state.build_id != self.plan.build_id or state.execution_id != self.execution_id:
            raise SchedulerError("Execution identity does not match the prepared plan")
        expected_ids = [task.task_id for task in self.plan.tasks]
        actual_ids = [item.task_id for item in state.tasks]
        folded = [task_id.casefold() for task_id in actual_ids]
        if len(folded) != len(set(folded)):
            raise SchedulerError("Execution state contains duplicate task IDs")
        if actual_ids != expected_ids:
            raise SchedulerError("Execution tasks do not match the prepared plan")
        statuses = {item.task_id: item.status for item in state.tasks}
        if sum(status == "running" for status in statuses.values()) > 1:
            raise SchedulerError("Sequential execution cannot have multiple running tasks")
        for task in self.plan.tasks:
            status = statuses[task.task_id]
            dependency_statuses = [statuses[dependency] for dependency in task.depends_on]
            if status in {"ready", "running", "completed", "failed"} and any(
                dependency != "completed" for dependency in dependency_statuses
            ):
                raise SchedulerError(
                    "Runnable or completed task has an incomplete dependency"
                )
            if status == "pending" and (
                not dependency_statuses
                or all(dependency == "completed" for dependency in dependency_statuses)
            ):
                raise SchedulerError("Pending task should be dependency-ready")
            if status == "blocked" and not any(
                dependency in {"failed", "blocked"}
                for dependency in dependency_statuses
            ):
                raise SchedulerError("Blocked task is not downstream of a failure")
        if state.status in {"completed", "failed", "interrupted"}:
            terminal = self.terminal_status(state)
            if terminal != state.status:
                raise SchedulerError("Execution terminal status contradicts task state")
        elif state.status == "running" and state.terminal_manifest_sha256 is not None:
            raise SchedulerError("Running execution cannot reference a terminal manifest")

    def next_ready(self, state: BuildExecutionState) -> BuildTask | None:
        for item in state.tasks:
            if item.status == "ready":
                return self.tasks[item.task_id]
        return None

    def mark_running(self, state: BuildExecutionState, task_id: str) -> BuildExecutionState:
        statuses = self._status_map(state)
        if statuses[task_id].status != "ready":
            raise SchedulerError("Only a ready task can start")
        return state.model_copy(
            update={
                "tasks": [
                    item.model_copy(update={"status": "running"})
                    if item.task_id == task_id
                    else item
                    for item in state.tasks
                ]
            }
        )

    def mark_completed(self, state: BuildExecutionState, task_id: str) -> BuildExecutionState:
        statuses = self._status_map(state)
        if statuses[task_id].status != "running":
            raise SchedulerError("Only a running task can complete")
        updated = [
            item.model_copy(update={"status": "completed"})
            if item.task_id == task_id
            else item
            for item in state.tasks
        ]
        status_map = {item.task_id: item.status for item in updated}
        released: list[TaskStateRecord] = []
        for item in updated:
            task = self.tasks[item.task_id]
            if item.status == "pending" and all(
                status_map[dependency] == "completed" for dependency in task.depends_on
            ):
                item = item.model_copy(update={"status": "ready"})
            released.append(item)
        # Terminal execution state is written only after durable evidence and a
        # terminal manifest exist.  The scheduler therefore remains running.
        return state.model_copy(update={"tasks": released, "status": "running"})

    def mark_failed(self, state: BuildExecutionState, task_id: str) -> BuildExecutionState:
        statuses = self._status_map(state)
        if statuses[task_id].status != "running":
            raise SchedulerError("Only a running task can fail")
        blocked = {task_id}
        changed = True
        while changed:
            changed = False
            for task in self.plan.tasks:
                if task.task_id not in blocked and any(
                    dependency in blocked for dependency in task.depends_on
                ):
                    blocked.add(task.task_id)
                    changed = True
        tasks: list[TaskStateRecord] = []
        for item in state.tasks:
            if item.task_id == task_id:
                tasks.append(item.model_copy(update={"status": "failed"}))
            elif item.task_id in blocked and item.status in {"pending", "ready"}:
                tasks.append(item.model_copy(update={"status": "blocked"}))
            else:
                tasks.append(item)
        # Independent ready branches remain runnable after this failure.
        return state.model_copy(update={"tasks": tasks, "status": "running"})

    @staticmethod
    def terminal_status(state: BuildExecutionState) -> str:
        statuses = {item.status for item in state.tasks}
        if any(status in {"pending", "ready", "running"} for status in statuses):
            raise SchedulerError("Execution still contains non-terminal tasks")
        if "failed" in statuses:
            return "failed"
        if "interrupted" in statuses:
            return "interrupted"
        if statuses == {"completed"}:
            return "completed"
        raise SchedulerError("Execution cannot be terminalized")

    @staticmethod
    def interrupt_running(state: BuildExecutionState) -> BuildExecutionState:
        if state.status != "running" and not any(
            item.status == "running" for item in state.tasks
        ):
            return state
        return state.model_copy(
            update={
                "status": "interrupted",
                "tasks": [
                    item.model_copy(update={"status": "interrupted"})
                    if item.status in {"pending", "ready", "running"}
                    else item
                    for item in state.tasks
                ],
            }
        )
