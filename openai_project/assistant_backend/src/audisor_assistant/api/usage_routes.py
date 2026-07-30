"""Usage API — canonical endpoint for token/capacity estimation.

POST /v1/usage/estimate — estimate the token cost of a request.

This endpoint is independent of the legacy /v1/chat lifecycle.
It uses only the provider's model metadata and the approximate
estimator — no ChatOrchestrator state, no provider calls.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from ..auth.ports import AuthContext
from ..providers.base import ProviderError
from ..usage.estimator import ApproximateTokenEstimator
from ..usage.models import PreparedCompletionRequest, PreparedMessage
from .dependencies import get_auth_context
from .routes import _get_chat_provider, _get_system_prompt

usage_router = APIRouter(prefix="/v1/usage")

_USAGE_ESTIMATOR = ApproximateTokenEstimator()
_ESTIMATE_MAX_TOKENS = 2048


class UsageEstimateRequest(BaseModel):
    """Request body for POST /v1/usage/estimate."""

    model_config = ConfigDict(extra="forbid")

    message: str = ""
    model: str | None = None
    history: list[dict[str, Any]] = Field(default_factory=list)


@usage_router.post("/estimate")
def usage_estimate(
    request: Request,
    payload: UsageEstimateRequest,
    auth: AuthContext = Depends(get_auth_context),
) -> JSONResponse:
    """Estimate the token cost of a hypothetical chat request.

    Never calls the completion provider — approximate estimation plus
    cached/best-effort model metadata only.

    This is the canonical estimate endpoint. The legacy /v1/chat/estimate
    is a compatibility alias that calls the same logic.
    """
    try:
        provider = _get_chat_provider(request)
    except ProviderError as exc:
        return JSONResponse(
            status_code=503,
            content={"error": exc.public_message, "category": exc.category.value},
        )

    model = provider.effective_model(payload.model)
    context_limit: int | None = None
    window = getattr(provider, "context_window", None)
    if callable(window):
        context_limit = window(model)

    system_prompt = _get_system_prompt(request)

    messages = (
        PreparedMessage(role="system", content=system_prompt),
        *(
            PreparedMessage(role=turn.get("role", "user"), content=turn.get("content", ""))
            for turn in payload.history
        ),
        PreparedMessage(role="user", content=payload.message),
    )

    prepared = PreparedCompletionRequest(
        operation_id="usage-estimate",
        attempt_id="1",
        provider=provider.provider_id,
        model=model,
        messages=messages,
        max_output_tokens=_ESTIMATE_MAX_TOKENS,
        context_limit=context_limit,
    )
    estimate = _USAGE_ESTIMATOR.estimate(prepared)

    usable = (
        max(context_limit - _ESTIMATE_MAX_TOKENS, 0)
        if context_limit is not None
        else None
    )
    return JSONResponse(
        status_code=200,
        content={
            "estimated_input_tokens": estimate.input_tokens,
            "reserved_output_tokens": estimate.reserved_output_tokens,
            "context_limit": context_limit,
            "usable_input_tokens": usable,
            "model": model,
            "method": estimate.method,
            "confidence": estimate.confidence.value,
        },
    )
