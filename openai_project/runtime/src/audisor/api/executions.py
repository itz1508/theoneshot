"""Prepared-build execution HTTP endpoint; execution policy lives below API."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from audisor.builder.authority import TargetAuthorityError, TargetAuthorityResolver
from audisor.builder.execution_store import (
    ExecutionConflictError,
    ExecutionStore,
    ExecutionStoreError,
)
from audisor.builder.executor import BuildExecutor
from audisor.builder.store import BuildStore
from audisor.builder.task_loader import (
    PreparedBuildBlockedError,
    PreparedBuildIntegrityError,
    PreparedBuildLoader,
    PreparedBuildNotFoundError,
)
from audisor.api.provider_errors import provider_http_exception
from audisor.routing.configuration import get_provider_router
from audisor.schemas.build import validate_safe_identifier
from audisor.schemas.execution import BuildExecutionRequest, BuildExecutionState
from audisor.schemas.errors import Declared422Response, ErrorResponse
from audisor.workers.base import ProviderError

router = APIRouter(prefix="/v1/builds", tags=["build-executions"])


@lru_cache(maxsize=1)
def get_build_executor() -> BuildExecutor:
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


@router.post(
    "/{build_id}/executions",
    response_model=BuildExecutionState,
    responses={
        404: {"model": ErrorResponse, "description": "Prepared build not found"},
        409: {"model": ErrorResponse, "description": "Prepared build or execution conflict"},
        422: {"model": Declared422Response, "description": "Invalid execution authority"},
        500: {"model": ErrorResponse, "description": "Execution persistence error"},
        502: {"model": ErrorResponse, "description": "Provider request failed"},
        503: {"model": ErrorResponse, "description": "Provider is not ready"},
    },
)
def execute_prepared_build(
    build_id: str,
    request: BuildExecutionRequest,
    executor: BuildExecutor = Depends(get_build_executor),
) -> BuildExecutionState:
    try:
        validate_safe_identifier(build_id, "build_id")
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_build_id", "message": "Invalid build ID"},
        ) from exc
    try:
        return executor.execute(build_id, request)
    except PreparedBuildNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "build_not_found", "message": "Prepared build not found"},
        ) from exc
    except PreparedBuildBlockedError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "build_blocked", "message": "Prepared build is blocked"},
        ) from exc
    except PreparedBuildIntegrityError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "prepared_integrity_error",
                "message": "Prepared build integrity verification failed",
            },
        ) from exc
    except ExecutionConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "execution_conflict", "message": str(exc)},
        ) from exc
    except TargetAuthorityError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "target_authority_error", "message": str(exc)},
        ) from exc
    except ProviderError as exc:
        raise provider_http_exception(exc) from exc
    except ExecutionStoreError as exc:
        raise HTTPException(
            status_code=500,
            detail={
                "code": "execution_storage_error",
                "message": "Execution storage failed",
            },
        ) from exc
