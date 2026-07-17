"""Dependency validation and deterministic topological ordering."""

from __future__ import annotations

import heapq

from audisor.schemas.build import BuildTask, validate_safe_identifier


class DependencyValidationError(ValueError):
    """The planning worker returned an invalid task dependency graph."""


def deterministic_topological_order(tasks: list[BuildTask]) -> list[BuildTask]:
    """Validate a task graph and order it with lexical task-ID tie breaking."""
    if not tasks:
        raise DependencyValidationError("ready plan has no tasks")

    tasks_by_id: dict[str, BuildTask] = {}
    ids_by_case: dict[str, str] = {}
    for task in tasks:
        validate_safe_identifier(task.task_id, "task_id")
        key = task.task_id.casefold()
        if key in ids_by_case:
            raise DependencyValidationError("task IDs must be unique on Windows")
        ids_by_case[key] = task.task_id
        tasks_by_id[task.task_id] = task

    adjacency: dict[str, list[str]] = {task_id: [] for task_id in tasks_by_id}
    indegree: dict[str, int] = {task_id: 0 for task_id in tasks_by_id}

    for task in tasks:
        seen_dependencies: set[str] = set()
        for dependency in task.depends_on:
            validate_safe_identifier(dependency, "dependency task_id")
            dependency_key = dependency.casefold()
            if dependency_key in seen_dependencies:
                raise DependencyValidationError(
                    f"task {task.task_id} contains a repeated dependency"
                )
            seen_dependencies.add(dependency_key)
            if dependency == task.task_id:
                raise DependencyValidationError(
                    f"task {task.task_id} cannot depend on itself"
                )
            if dependency not in tasks_by_id:
                raise DependencyValidationError(
                    f"task {task.task_id} depends on an unknown task"
                )
            adjacency[dependency].append(task.task_id)
            indegree[task.task_id] += 1

    roots = [task_id for task_id, count in indegree.items() if count == 0]
    if not roots:
        raise DependencyValidationError("dependency cycle leaves no root task")

    available = [(task_id.casefold(), task_id) for task_id in roots]
    heapq.heapify(available)
    ordered_ids: list[str] = []

    while available:
        _, task_id = heapq.heappop(available)
        ordered_ids.append(task_id)
        for dependent in sorted(adjacency[task_id], key=lambda value: (value.casefold(), value)):
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                heapq.heappush(available, (dependent.casefold(), dependent))

    if len(ordered_ids) != len(tasks):
        raise DependencyValidationError("dependency cycle detected")

    return [
        tasks_by_id[task_id].model_copy(
            update={
                "depends_on": sorted(
                    tasks_by_id[task_id].depends_on,
                    key=lambda value: (value.casefold(), value),
                )
            }
        )
        for task_id in ordered_ids
    ]

