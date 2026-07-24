"""Audisor synonym generation HTTP endpoint — tombstoned in 0.10.0.

Factory functions are retained with lazy imports for direct Python API
compatibility.
"""

from functools import lru_cache
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter()

_DEPRECATION_BODY = {
    "code": "legacy_runtime_deprecated",
    "message": "The Audisor model-execution runtime is deprecated.",
    "removal_version": "1.0.0",
}


@router.post("/v1/synonyms", deprecated=True, responses={410: {"description": "Legacy runtime deprecated"}})
def generate_synonyms() -> JSONResponse:
    return JSONResponse(status_code=410, content=_DEPRECATION_BODY)


# ---------------------------------------------------------------------------
# Legacy compatibility factories — lazy imports keep the module load-free
# of the legacy dependency graph.
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def get_synonym_service() -> Any:
    from audisor.routing.configuration import get_provider_router
    from audisor.synonyms import SynonymService

    return SynonymService(router=get_provider_router())


def render_synonyms(response: object) -> str:
    """Compatibility helper for already validated synonym responses."""
    from audisor.synonyms import render_synonym_response

    return render_synonym_response(response)  # type: ignore[arg-type]
