"""Assistant API routes.

One provider-neutral endpoint: POST /v1/assistant/requests.
Authentication is enforced through the AuthProvider boundary; validation
errors surface as 422; provider failures surface as normalized envelopes.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from ..application.service import AssistantService
from ..auth.ports import AuthContext
from ..schemas.requests import AssistantRequest
from ..schemas.responses import AssistantResponse
from .dependencies import get_auth_context, get_service

router = APIRouter(prefix="/v1/assistant")


@router.post("/requests", response_model=AssistantResponse)
def create_assistant_request(
    payload: AssistantRequest,
    auth: AuthContext = Depends(get_auth_context),
    service: AssistantService = Depends(get_service),
) -> AssistantResponse:
    return service.handle(payload)
