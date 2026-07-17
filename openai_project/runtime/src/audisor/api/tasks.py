"""Provider-neutral task HTTP endpoint."""

from functools import lru_cache

from fastapi import APIRouter, Depends

from audisor.api.provider_errors import provider_http_exception
from audisor.routing.configuration import get_provider_router
from audisor.schemas.errors import Declared422Response, ErrorResponse
from audisor.schemas.task_input import TaskInputBatch
from audisor.schemas.task_output import TaskOutput
from audisor.service import TaskService
from audisor.workers.base import ProviderError

router = APIRouter()


@lru_cache(maxsize=1)
def get_task_service() -> TaskService:
    return TaskService(router=get_provider_router())


@router.post(
    "/v1/tasks",
    response_model=list[TaskOutput],
    responses={
        422: {"model": Declared422Response, "description": "Invalid task batch"},
        502: {"model": ErrorResponse, "description": "Provider request failed"},
        503: {"model": ErrorResponse, "description": "Provider is not ready"},
    },
)
def execute_tasks(
    batch: TaskInputBatch,
    service: TaskService = Depends(get_task_service),
) -> list[TaskOutput]:
    try:
        return service.execute_tasks(batch.root)
    except ProviderError as exc:
        raise provider_http_exception(exc) from exc
