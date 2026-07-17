"""Provider-neutral task execution and typed result verification."""

from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from typing import cast

from audisor.routing.router import ProviderRouter
from audisor.schemas.task_input import TaskInput
from audisor.schemas.task_output import TaskOutput
from audisor.workers.base import (
    ProviderCapabilityError,
    ProviderInvalidResponseError,
    WorkerProvider,
)


class TaskService:
    """Execute validated text tasks through one explicitly selected provider."""

    def __init__(self, router: ProviderRouter, max_workers: int = 4) -> None:
        if max_workers < 1:
            raise ValueError("max_workers must be at least 1")
        self._router = router
        self._max_workers = max_workers

    @staticmethod
    def _execute_one(task: TaskInput, provider: WorkerProvider) -> TaskOutput:
        output = provider.execute(task)
        if not isinstance(output, TaskOutput):
            raise ProviderInvalidResponseError(
                "Selected provider returned an invalid response",
                internal_detail=f"result_type={type(output).__name__}",
            )
        if output.task_id != task.task_id:
            raise ProviderInvalidResponseError(
                "Selected provider returned an invalid response",
                internal_detail="task_id=mismatch",
            )
        if not output.answer.strip():
            raise ProviderInvalidResponseError(
                "Selected provider returned an invalid response",
                internal_detail="answer=empty",
            )
        return output

    def execute_tasks(self, tasks: list[TaskInput]) -> list[TaskOutput]:
        provider = self._router.select_provider()
        if not provider.capabilities().text:
            raise ProviderCapabilityError(
                "Selected provider does not support text tasks",
                internal_detail="required=text",
            )
        worker_count = min(len(tasks), self._max_workers)
        ordered_results: list[TaskOutput | None] = [None] * len(tasks)
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures: dict[Future[TaskOutput], int] = {
                executor.submit(self._execute_one, task, provider): index
                for index, task in enumerate(tasks)
            }
            try:
                for future in as_completed(futures):
                    ordered_results[futures[future]] = future.result()
            except Exception:
                for pending in futures:
                    pending.cancel()
                raise
        return [cast(TaskOutput, result) for result in ordered_results]
