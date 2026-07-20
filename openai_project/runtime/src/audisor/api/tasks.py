"""Provider-neutral task HTTP endpoint."""

from functools import lru_cache
from typing import Any, Mapping

from fastapi import APIRouter, Depends, HTTPException

from audisor.adapters.protocol import AudisorOperationRequest
from audisor.api.provider_errors import provider_http_exception
from audisor.operations.transport import canonical_operation_service
from audisor.routing.configuration import get_provider_router
from audisor.schemas.errors import Declared422Response, ErrorResponse
from audisor.schemas.task_input import TaskInput, TaskInputBatch
from audisor.schemas.task_output import TaskOutput
from audisor.service import TaskService
from audisor.workers.base import ProviderError

router = APIRouter()
canonical_router = APIRouter(prefix="/v1/operations", tags=["canonical-operations"])


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


@lru_cache(maxsize=1)
def get_canonical_operation_service() -> Any:
    """Return the host-agnostic canonical operation service."""
    return canonical_operation_service()


@canonical_router.post(
    "/tasks",
    response_model=dict,
    responses={
        422: {"model": Declared422Response, "description": "Invalid task batch"},
        500: {"model": ErrorResponse, "description": "Operation execution failed"},
    },
)
def execute_canonical_tasks(
    batch: TaskInputBatch,
    service: Any = Depends(get_canonical_operation_service),
) -> Mapping[str, Any]:
    """Host-agnostic task analysis endpoint.

    Accepts a batch of TaskInput items, submits each as a canonical
    'analyze' operation through AudisorOperationExecutor, and returns
    a consolidated response.
    """
    try:
        results: list[Mapping[str, Any]] = []
        for task in batch.root:
            canonical_request = AudisorOperationRequest(
                operation_id=task.task_id,
                mode="analyze",
                request={"prompt": task.prompt},
                authority=_default_authority(),
                constraints={},
                host_capabilities=_default_capabilities(),
                host_context={"adapter": "api"},
            )
            result = service._executor.execute(canonical_request)
            results.append(result.to_mapping())
        return {"operation_id": "batch", "results": results}
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={
                "code": "operation_execution_failed",
                "message": f"{type(exc).__name__}: {exc}",
            },
        ) from exc


def _default_authority() -> Any:
    """Return a default authority context for API task requests."""
    from audisor.schemas.authority import AuthorityContext, AuthoritySource, PermissionSet
    return AuthorityContext(
        source=AuthoritySource(
            source_type="host_adapter",
            grant_id="api",
            host_identity="api",
        ),
        permissions=PermissionSet(
            allowed_paths=["."],
            prohibited_paths=[".git", ".codex"],
            allowed_tools=["read_file"],
            prohibited_tools=["write_file", "execute_command", "delete_file"],
        ),
        scope="repository",
    )


def _default_capabilities() -> Any:
    """Return default host capabilities for API task requests."""
    from audisor.adapters.protocol import HostCapabilities
    return HostCapabilities(
        supports_streaming=False,
        supports_tools=True,
        supports_artifacts=False,
    )
