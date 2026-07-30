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
from ..application.chat_orchestrator import (
    ChatOrchestrator,
    ChatTurnApprovalRequired,
    ChatTurnFinal,
    ChatTurnPending,
    ToolCallEvent as OrchestratorToolCallEvent,
)
from ..application.service import AssistantService
from ..auth.ports import AuthContext
from ..providers.base import HistoryMessage, ProviderError
from ..schemas.chat import (
    ChatApprovalRequired,
    ChatContinueRequest,
    ChatErrorResponse,
    ChatEstimateRequest,
    ChatEstimateResponse,
    ChatRequest,
    ChatResponse,
    ChatToolCallsPending,
    ChatUsage,
    ToolCallEventResponse,
)
from ..schemas.requests import AssistantRequest
from ..schemas.responses import (
    AssistantHealthResponse,
    AssistantModelsResponse,
    AssistantResponse,
    ProviderInfo,
)
from ..tools import default_registry
from ..tools.schemas import ToolResult
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

#: Chat orchestrator singleton
_CHAT_ORCHESTRATOR = ChatOrchestrator(registry=default_registry)


def _get_chat_clock(request: Request) -> OperatorChatClock:
    """Resolve the operator-chat clock from app.state.

    Tests inject via ``app.state.operator_chat_clock``; production uses
    the default clock set at startup.
    """
    return getattr(request.app.state, "operator_chat_clock", None) or default_clock


@chat_router.post("", response_model=None)
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

    system_prompt = _get_system_prompt(request, tools_available=payload.workspace_available)

    # Use orchestrator for tool-calling flow
    result = _CHAT_ORCHESTRATOR.execute_turn(
        provider=provider,
        system_prompt=system_prompt,
        user_prompt=payload.message,
        history=tuple(
            HistoryMessage(role=turn.role, content=turn.content)
            for turn in payload.history
        ),
        workspace_available=payload.workspace_available,
        model_override=payload.model,
        max_tokens=_CHAT_MAX_TOKENS,
        timeout_seconds=_CHAT_TIMEOUT_SECONDS,
    )

    return _turn_result_to_response(result, provider, payload.model)


@chat_router.post("/continue")
def operator_chat_continue(
    request: Request,
    payload: ChatContinueRequest,
    auth: AuthContext = Depends(get_auth_context),
) -> JSONResponse:
    """Resume a turn after frontend tool execution or approval.

    Returns 200 ChatResponse, or 202 ChatToolCallsPending/ChatApprovalRequired.
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

    # Convert submitted results to internal ToolResult objects
    tool_results = [
        ToolResult(
            call_id=r.call_id,
            name=r.tool_name,
            output=r.output,
            error=r.error,
            status=r.status,
        )
        for r in payload.tool_results
    ]

    result = _CHAT_ORCHESTRATOR.continue_turn(
        turn_id=payload.turn_id,
        tool_results=tool_results,
    )

    return _turn_result_to_response(result, provider, None)


def _get_system_prompt(request: Request, *, tools_available: bool = False) -> str:
    """Build the operator-chat system prompt from the app's clock.

    Called at request time — never at module load — so the date is
    always fresh.
    """
    return build_operator_chat_system_prompt(
        _get_chat_clock(request), tools_available=tools_available
    )


def _orchestrator_event_to_response(event: OrchestratorToolCallEvent) -> ToolCallEventResponse:
    """Convert internal orchestrator event to API response model."""
    return ToolCallEventResponse(
        call_id=event.call_id,
        tool_name=event.tool_name,
        arguments=event.arguments,
        executor=event.executor,
        status=event.status,
        turn_id=event.turn_id,
        operation_id=event.operation_id,
        output=event.output,
        error=event.error,
        duration_ms=event.duration_ms,
    )


def _turn_result_to_response(
    result, provider, model_override: str | None
) -> ChatResponse | JSONResponse:
    """Convert a ChatTurnResult to the appropriate HTTP response."""
    if isinstance(result, ChatTurnFinal):
        input_tokens = result.usage.get("prompt_tokens", 0)
        output_tokens = result.usage.get("completion_tokens", 0)
        trace = (
            [_orchestrator_event_to_response(e) for e in result.tool_trace]
            if result.tool_trace
            else None
        )
        return ChatResponse(
            reply=result.reply,
            provider=ProviderInfo(id=provider.provider_id, source=provider.source),
            model=provider.effective_model(model_override),
            usage=ChatUsage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=input_tokens + output_tokens,
                cost=None,
                provider_type="cloud" if provider.source == "cloud" else "local",
            ),
            tool_trace=trace,
        )

    elif isinstance(result, ChatTurnPending):
        return JSONResponse(
            status_code=202,
            content=ChatToolCallsPending(
                turn_id=result.turn_id,
                pending_calls=[_orchestrator_event_to_response(e) for e in result.pending_calls],
                completed_calls=[_orchestrator_event_to_response(e) for e in result.completed_calls],
                loop_iteration=result.loop_iteration,
                max_loops=result.max_loops,
            ).model_dump(),
        )

    elif isinstance(result, ChatTurnApprovalRequired):
        return JSONResponse(
            status_code=202,
            content=ChatApprovalRequired(
                turn_id=result.turn_id,
                tool_call=_orchestrator_event_to_response(result.tool_call),
                reason=result.reason,
                risk_level=result.risk_level,
            ).model_dump(),
        )

    # Fallback (should not happen)
    return JSONResponse(
        status_code=500,
        content={"error": "Unexpected orchestrator result type"},
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
