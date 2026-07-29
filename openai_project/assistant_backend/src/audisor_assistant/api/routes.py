"""Assistant API routes.

Provider-neutral endpoints under /v1/assistant:
- POST /requests  (auth) one assistant completion
- GET  /models    (auth) models selectable within the fixed provider
- GET  /health    (no auth) liveness probe
Authentication is enforced through the AuthProvider boundary; validation
errors surface as 422; provider failures surface as normalized envelopes.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from ..application.service import AssistantService
from ..auth.ports import AuthContext
from ..schemas.requests import AssistantRequest
from ..schemas.responses import (
    AssistantHealthResponse,
    AssistantModelsResponse,
    AssistantResponse,
)
from .dependencies import get_auth_context, get_service

router = APIRouter(prefix="/v1/assistant")


@router.post("/requests", response_model=AssistantResponse)
def create_assistant_request(
    payload: AssistantRequest,
    auth: AuthContext = Depends(get_auth_context),
    service: AssistantService = Depends(get_service),
) -> AssistantResponse:
    return service.handle(payload)


@router.get("/models", response_model=AssistantModelsResponse)
def list_assistant_models(
    auth: AuthContext = Depends(get_auth_context),
    service: AssistantService = Depends(get_service),
) -> AssistantModelsResponse:
    return service.describe_models()


@router.get("/health", response_model=AssistantHealthResponse)
def assistant_health(
    service: AssistantService = Depends(get_service),
) -> AssistantHealthResponse:
    return service.describe_health()
