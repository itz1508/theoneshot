"""Build preparation HTTP endpoint. This module owns HTTP concerns only."""

from functools import lru_cache

from fastapi import APIRouter, Depends, HTTPException

from audisor.builder.preparer import BuildPreparer
from audisor.builder.store import (
    BuildAlreadyExistsError,
    BuildStore,
    BuildStoreError,
)
from audisor.api.provider_errors import provider_http_exception
from audisor.routing.configuration import get_provider_router
from audisor.schemas.build import BuildPlan, BuildRequest
from audisor.schemas.errors import Declared422Response, ErrorResponse
from audisor.workers.base import ProviderError

router = APIRouter(prefix="/v1/builds", tags=["builds"])


@lru_cache(maxsize=1)
def get_build_preparer() -> BuildPreparer:
    """Build the configured planning boundary without selecting a worker yet."""
    return BuildPreparer(
        router=get_provider_router(),
        store=BuildStore.from_environment(),
    )


@router.post(
    "/prepare",
    response_model=BuildPlan,
    responses={
        409: {"model": ErrorResponse, "description": "Build already exists"},
        422: {"model": Declared422Response, "description": "Invalid build request"},
        500: {"model": ErrorResponse, "description": "Build storage error"},
        502: {"model": ErrorResponse, "description": "Provider returned an invalid plan"},
        503: {"model": ErrorResponse, "description": "Provider is not ready"},
    },
)
def prepare_build(
    request: BuildRequest,
    preparer: BuildPreparer = Depends(get_build_preparer),
) -> BuildPlan:
    """Prepare and persist a validated ready or blocked build."""
    try:
        return preparer.prepare(request)
    except BuildAlreadyExistsError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "build_exists", "message": "Build already exists"},
        ) from exc
    except ProviderError as exc:
        raise provider_http_exception(exc) from exc
    except BuildStoreError as exc:
        raise HTTPException(
            status_code=500,
            detail={"code": "build_storage_error", "message": "Build storage failed"},
        ) from exc
