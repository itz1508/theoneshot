"""Audisor synonym generation through the existing task envelope."""

from functools import lru_cache

from fastapi import APIRouter, Depends

from audisor.api.provider_errors import provider_http_exception
from audisor.routing.configuration import get_provider_router
from audisor.schemas.errors import Declared422Response, ErrorResponse
from audisor.schemas.task_input import TaskInputBatch
from audisor.schemas.task_output import TaskOutput
from audisor.synonyms import SynonymService, render_synonym_response
from audisor.workers.base import ProviderError

router = APIRouter()


@lru_cache(maxsize=1)
def get_synonym_service() -> SynonymService:
    return SynonymService(router=get_provider_router())


@router.post(
    "/v1/synonyms",
    response_model=list[TaskOutput],
    responses={
        422: {"model": Declared422Response, "description": "Invalid synonym task batch"},
        502: {"model": ErrorResponse, "description": "Provider request failed"},
        503: {"model": ErrorResponse, "description": "Provider is not ready"},
    },
)
def generate_synonyms(
    batch: TaskInputBatch,
    service: SynonymService = Depends(get_synonym_service),
) -> list[TaskOutput]:
    try:
        return [service.generate_task(task) for task in batch.root]
    except ProviderError as exc:
        raise provider_http_exception(exc) from exc


def render_synonyms(response: object) -> str:
    """Compatibility helper for already validated synonym responses."""
    return render_synonym_response(response)  # type: ignore[arg-type]
