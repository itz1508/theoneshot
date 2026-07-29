"""Assistant API routes.

Provider-neutral endpoints:
- POST /v1/assistant/requests  (auth) one assistant completion
- GET  /v1/assistant/models    (auth) models selectable within the fixed provider
- GET  /v1/assistant/health    (no auth) liveness probe
- POST /v1/chat                (auth) operator chat — raw completion with real token usage

Authentication is enforced through the AuthProvider boundary; validation
errors surface as 422; provider failures surface as normalized envelopes.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from ..application.operator_chat_prompt import (
    OperatorChatClock,
    build_operator_chat_system_prompt,
    default_clock,
)
from ..application.service import AssistantService
from ..auth.ports import AuthContext
from ..providers.base import CompletionRequest, HistoryMessage, ProviderError
from ..schemas.chat import (
    ChatErrorResponse,
    ChatEstimateRequest,
    ChatEstimateResponse,
    ChatRequest,
    ChatResponse,
    ChatUsage,
)
from ..schemas.requests import AssistantRequest
from ..schemas.responses import (
    AssistantHealthResponse,
    AssistantModelsResponse,
    AssistantResponse,
    ProviderInfo,
)
from ..usage.estimator import ApproximateTokenEstimator
from ..usage.models import PreparedCompletionRequest, PreparedMessage
from .dependencies import build_provider, get_auth_context, get_service


def _get_chat_provider(request: Request):
    """Resolve provider for /v1/chat. Uses test-injected service's provider
    when available, otherwise builds from environment."""
    service = getattr(request.app.state, "service", None)
    if service is not None:
        return service.provider
    return build_provider()

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


# ─── Operator Chat ───────────────────────────────────────────────────────────

chat_router = APIRouter(prefix="/v1/chat")

_CHAT_MAX_TOKENS = 2048
_CHAT_TIMEOUT_SECONDS = 120.0

#: Shared approximate estimator — same infrastructure usage accounting uses.
_CHAT_ESTIMATOR = ApproximateTokenEstimator()


def _get_chat_clock(request: Request) -> OperatorChatClock:
    """Resolve the operator-chat clock from app.state.

    Tests inject via ``app.state.operator_chat_clock``; production uses
    the default clock set at startup.
    """
    return getattr(request.app.state, "operator_chat_clock", None) or default_clock


def _get_system_prompt(request: Request) -> str:
    """Build the operator-chat system prompt from the app’s clock.

    Called at request time — never at module load — so the date is
    always fresh.
    """
    return build_operator_chat_system_prompt(_get_chat_clock(request))


@chat_router.post("", response_model=ChatResponse)
def operator_chat(
    request: Request,
    payload: ChatRequest,
    auth: AuthContext = Depends(get_auth_context),
) -> ChatResponse | JSONResponse:
    try:
        provider = _get_chat_provider(request)
    except ProviderError as exc:
        return JSONResponse(
            status_code=503,
            content=ChatErrorResponse(
                error=exc.public_message, category=exc.category.value
            ).model_dump(),
        )

    system_prompt = _get_system_prompt(request)
    completion = CompletionRequest(
        system_prompt=system_prompt,
        user_prompt=payload.message,
        max_tokens=_CHAT_MAX_TOKENS,
        timeout_seconds=_CHAT_TIMEOUT_SECONDS,
        purpose="operator_chat",
        model_override=payload.model,
        history=tuple(
            HistoryMessage(role=turn.role, content=turn.content)
            for turn in payload.history
        ),
    )

    try:
        reply = provider.complete(completion)
    except ProviderError as exc:
        return JSONResponse(
            status_code=503,
            content=ChatErrorResponse(
                error=exc.public_message, category=exc.category.value
            ).model_dump(),
        )

    raw_usage = reply.usage or {}
    input_tokens = raw_usage.get("prompt_tokens", 0)
    output_tokens = raw_usage.get("completion_tokens", 0)

    return ChatResponse(
        reply=reply.text,
        provider=ProviderInfo(id=provider.provider_id, source=provider.source),
        model=reply.model or provider.effective_model(payload.model),
        usage=ChatUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
            cost=None,  # local providers have no cost
            provider_type="cloud" if provider.source == "cloud" else "local",
        ),
    )


@chat_router.post("/estimate", response_model=ChatEstimateResponse)
def operator_chat_estimate(
    request: Request,
    payload: ChatEstimateRequest,
    auth: AuthContext = Depends(get_auth_context),
) -> ChatEstimateResponse | JSONResponse:
    """Live composer capacity preview: estimate the complete next /v1/chat
    request input (system prompt + history + draft) and report the
    authoritative context window for the effective model.

    Never calls the completion provider — approximate estimation plus
    cached/best-effort model metadata only.
    """
    try:
        provider = _get_chat_provider(request)
    except ProviderError as exc:
        return JSONResponse(
            status_code=503,
            content=ChatErrorResponse(
                error=exc.public_message, category=exc.category.value
            ).model_dump(),
        )

    model = provider.effective_model(payload.model)
    context_limit: int | None = None
    window = getattr(provider, "context_window", None)
    if callable(window):
        context_limit = window(payload.model)

    system_prompt = _get_system_prompt(request)
    prepared = PreparedCompletionRequest(
        operation_id="chat-estimate",
        attempt_id="1",
        provider=provider.provider_id,
        model=model,
        messages=(
            PreparedMessage(role="system", content=system_prompt),
            *(
                PreparedMessage(role=turn.role, content=turn.content)
                for turn in payload.history
            ),
            PreparedMessage(role="user", content=payload.message),
        ),
        max_output_tokens=_CHAT_MAX_TOKENS,
        context_limit=context_limit,
    )
    estimate = _CHAT_ESTIMATOR.estimate(prepared)

    usable = (
        max(context_limit - _CHAT_MAX_TOKENS, 0)
        if context_limit is not None
        else None
    )
    return ChatEstimateResponse(
        estimated_input_tokens=estimate.input_tokens,
        reserved_output_tokens=estimate.reserved_output_tokens,
        context_limit=context_limit,
        usable_input_tokens=usable,
        model=model,
        method=estimate.method,
        confidence=estimate.confidence.value,
    )
