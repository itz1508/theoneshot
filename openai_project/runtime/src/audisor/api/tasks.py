"""Provider-neutral task HTTP endpoint — tombstoned in 0.10.0."""

from functools import lru_cache
from typing import Any, Mapping

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter()
canonical_router = APIRouter(prefix="/v1/operations", tags=["canonical-operations"])

_DEPRECATION_BODY = {
    "code": "legacy_runtime_deprecated",
    "message": "The Audisor model-execution runtime is deprecated.",
    "removal_version": "1.0.0",
}


@router.post("/v1/tasks", deprecated=True, responses={410: {"description": "Legacy runtime deprecated"}})
def execute_tasks() -> JSONResponse:
    return JSONResponse(status_code=410, content=_DEPRECATION_BODY)


@canonical_router.post("/tasks", deprecated=True, responses={410: {"description": "Legacy runtime deprecated"}})
def execute_canonical_tasks() -> JSONResponse:
    return JSONResponse(status_code=410, content=_DEPRECATION_BODY)


# ---------------------------------------------------------------------------
# Legacy compatibility factories — imports are lazy so the module loads
# without initialising the legacy dependency graph.  Retained for direct
# Python API compatibility; not wired to any HTTP handler in 0.10.0.
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def get_task_service() -> Any:
    from audisor.routing.configuration import get_provider_router
    from audisor.service import TaskService

    return TaskService(router=get_provider_router())


@lru_cache(maxsize=1)
def get_canonical_operation_service() -> Any:
    """Return the host-agnostic canonical operation service."""
    from audisor.operations.transport import canonical_operation_service

    return canonical_operation_service()


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
