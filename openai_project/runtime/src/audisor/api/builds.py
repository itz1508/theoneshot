"""Build preparation HTTP endpoint — tombstoned in 0.10.0.

This module owns HTTP concerns only.  Factory functions are retained with
lazy imports for direct Python API compatibility.
"""

from functools import lru_cache
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/v1/builds", tags=["builds"])

_DEPRECATION_BODY = {
    "code": "legacy_runtime_deprecated",
    "message": "The Audisor model-execution runtime is deprecated.",
    "removal_version": "1.0.0",
}


@router.post("/prepare", deprecated=True, responses={410: {"description": "Legacy runtime deprecated"}})
def prepare_build() -> JSONResponse:
    return JSONResponse(status_code=410, content=_DEPRECATION_BODY)


# ---------------------------------------------------------------------------
# Legacy compatibility factories — lazy imports keep the module load-free
# of the legacy dependency graph.  Retained for direct Python API use.
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def get_build_preparer() -> Any:
    """Build the configured planning boundary without selecting a worker yet."""
    from audisor.builder.preparer import BuildPreparer
    from audisor.builder.store import BuildStore
    from audisor.routing.configuration import get_provider_router

    return BuildPreparer(
        router=get_provider_router(),
        store=BuildStore.from_environment(),
    )
