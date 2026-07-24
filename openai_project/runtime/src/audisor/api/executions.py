"""Prepared-build execution HTTP endpoint — tombstoned in 0.10.0.

Execution policy lives below API.  Factory functions are retained with
lazy imports for direct Python API compatibility.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/v1/builds", tags=["build-executions"])
canonical_router = APIRouter(prefix="/v1/operations", tags=["canonical-operations"])

_DEPRECATION_BODY = {
    "code": "legacy_runtime_deprecated",
    "message": "The Audisor model-execution runtime is deprecated.",
    "removal_version": "1.0.0",
}


@router.post("/{build_id}/executions", deprecated=True, responses={410: {"description": "Legacy runtime deprecated"}})
def execute_prepared_build(build_id: str) -> JSONResponse:
    return JSONResponse(status_code=410, content=_DEPRECATION_BODY)


@canonical_router.post("", deprecated=True, responses={410: {"description": "Legacy runtime deprecated"}})
def execute_canonical_operation() -> JSONResponse:
    return JSONResponse(status_code=410, content=_DEPRECATION_BODY)


# ---------------------------------------------------------------------------
# Legacy compatibility factories — lazy imports keep the module load-free
# of the legacy dependency graph.  Retained for direct Python API use.
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def get_build_executor() -> Any:
    import os
    from pathlib import Path

    from audisor.builder.authority import TargetAuthorityResolver
    from audisor.builder.execution_store import ExecutionStore
    from audisor.builder.executor import BuildExecutor
    from audisor.builder.store import BuildStore
    from audisor.builder.task_loader import PreparedBuildLoader
    from audisor.routing.configuration import get_provider_router

    build_store = BuildStore.from_environment()
    configured = os.environ.get("AUDISOR_ALLOWED_TARGET_ROOTS", "").strip()
    approved_roots = tuple(
        Path(item).expanduser()
        for item in configured.split(os.pathsep)
        if item.strip()
    )
    return BuildExecutor(
        router=get_provider_router(),
        loader=PreparedBuildLoader(build_store),
        authority=TargetAuthorityResolver(
            data_dir=build_store.data_dir,
            approved_target_roots=approved_roots,
        ),
        store=ExecutionStore(data_dir=build_store.data_dir),
    )


@lru_cache(maxsize=1)
def get_canonical_operation_service() -> Any:
    """Return the host-agnostic canonical operation service."""
    from audisor.operations.transport import canonical_operation_service

    return canonical_operation_service()
