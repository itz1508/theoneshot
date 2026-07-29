"""Assistant application service.

Orchestrates one request: mode gating, prompt construction, a single
provider call (no silent fallback), contract validation of the provider
output, sanitization, usage accounting, and the public response envelope.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from pydantic import ValidationError

if TYPE_CHECKING:  # runtime import stays one-directional (fix_engines -> service)
    from .fix_engines import FixEngineSelector

from ..domain.modes import AssistantMode, mode_requires_selected_text
from ..domain.results import MODE_RESULT_MODELS, SelectionRequiredResult
from ..policies.privacy import (
    sanitize_diagram_code,
    sanitize_public_message,
    sanitized_request_record,
)
from ..providers.base import (
    AssistantProvider,
    CompletionReply,
    CompletionRequest,
    ModelListing,
    ProviderError,
)
from ..schemas.requests import AssistantRequest
from ..schemas.responses import (
    AssistantHealthResponse,
    AssistantModelsResponse,
    AssistantResponse,
    AssistantStatus,
    ProviderInfo,
    PublicErrorCategory,
)
from ..usage.public import UsageAccountingEvidence
from .prompts import build_system_prompt, build_user_prompt
from .usage_integration import (
    UsageAccountingIntegration,
    UsageAttempt,
    begin_attempt,
    finalize_failure,
    finalize_reply,
)

__all__ = [
    "AssistantService",
    "build_system_prompt",
    "build_user_prompt",
]

logger = logging.getLogger("audisor_assistant")

_JSON_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def _extract_json(text: str) -> dict:
    cleaned = _JSON_FENCE.sub("", text.strip()).strip()
    payload = json.loads(cleaned)
    if not isinstance(payload, dict):
        raise ValueError("provider payload is not an object")
    return payload


def _sanitize_visualize(result: dict) -> list[str]:
    """Sanitize workflow diagram code in place; layout/unclear pass through."""
    if not result.get("diagram_code"):
        return []
    sanitized_code, diagram_warnings = sanitize_diagram_code(result["diagram_code"])
    result["diagram_code"] = sanitized_code
    return list(diagram_warnings)


def _failure(
    request: AssistantRequest,
    provider_info: ProviderInfo,
    error: ProviderError,
    *,
    usage: dict[str, int] | None = None,
    accounting: UsageAccountingEvidence | None = None,
) -> AssistantResponse:
    message = sanitize_public_message(error.public_message)
    return AssistantResponse(
        request_id=request.request_id,
        mode=request.mode,
        status=AssistantStatus.FAILED,
        result={"error": {"category": error.category.value, "message": message}},
        provider=provider_info,
        usage=usage,
        uncertainty=[message],
        accounting=accounting,
    )


def _log(
    request: AssistantRequest,
    response: AssistantResponse,
    started_at: datetime,
) -> None:
    # Only the sanitized metadata record is logged — never user text.
    record = sanitized_request_record(
        request_id=request.request_id,
        mode=request.mode.value,
        status=response.status.value,
        provider_source=response.provider.source if response.provider else None,
        started_at=started_at,
        usage=response.usage,
    )
    logger.info("assistant_request %s", json.dumps(record))


def _run_model_mode(
    request: AssistantRequest,
    *,
    provider: AssistantProvider,
    accounting: UsageAccountingIntegration | None,
    max_tokens: int,
    timeout_seconds: float,
) -> AssistantResponse:
    """Single provider invocation with exactly one accounted attempt."""
    provider_info = ProviderInfo(id=provider.provider_id, source=provider.source)
    completion = CompletionRequest(
        mode=request.mode,
        system_prompt=build_system_prompt(request.mode),
        user_prompt=build_user_prompt(request),
        max_tokens=max_tokens,
        timeout_seconds=timeout_seconds,
        model_override=request.model,
    )
    attempt = begin_attempt(accounting, provider, completion, request.request_id)
    try:
        reply = provider.complete(completion)
    except ProviderError as error:
        return _failure(
            request, provider_info, error, accounting=finalize_failure(attempt)
        )
    except Exception:  # pragma: no cover - defensive boundary
        return _failure(
            request,
            provider_info,
            ProviderError(
                PublicErrorCategory.INTERNAL, "The assistant request failed."
            ),
            accounting=finalize_failure(attempt),
        )
    return _model_reply_response(
        request, provider_info, reply, finalize_reply(attempt, reply)
    )


def _model_reply_response(
    request: AssistantRequest,
    provider_info: ProviderInfo,
    reply: CompletionReply,
    evidence: UsageAccountingEvidence | None,
) -> AssistantResponse:
    try:
        payload = _extract_json(reply.text)
        result_model = MODE_RESULT_MODELS[request.mode].model_validate(payload)
    except (ValueError, ValidationError):
        return _failure(
            request,
            provider_info,
            ProviderError(
                PublicErrorCategory.INVALID_RESPONSE,
                "Provider returned a response that does not match the "
                "mode contract.",
            ),
            usage=reply.usage,
            accounting=evidence,
        )

    result = result_model.model_dump()
    warnings: list[str] = []
    if request.mode is AssistantMode.VISUALIZE_DESIGN:
        warnings.extend(_sanitize_visualize(result))

    uncertainty = [
        str(item) for item in result.get("uncertainty", []) if str(item).strip()
    ]
    status = (
        AssistantStatus.UNCERTAINTY if uncertainty else AssistantStatus.COMPLETED
    )
    engine: str | None = None
    if request.mode is AssistantMode.FIX_WORDING:
        # Generic path is always the model engine; providers cannot claim
        # another engine's semantic shape, so the envelope/result pair
        # never disagrees.
        result["result_kind"] = "model"
        engine = "model"
    return AssistantResponse(
        request_id=request.request_id,
        mode=request.mode,
        status=status,
        result=result,
        warnings=warnings,
        provider=provider_info,
        usage=reply.usage,
        engine=engine,
        uncertainty=uncertainty,
        accounting=evidence,
    )


class AssistantService:
    def __init__(
        self,
        provider: AssistantProvider,
        *,
        max_tokens: int = 0,
        timeout_seconds: float = 0.0,
        fix_selector: "FixEngineSelector | None" = None,
        accounting: UsageAccountingIntegration | None = None,
    ) -> None:
        self._provider = provider
        self._max_tokens = max_tokens
        self._timeout_seconds = timeout_seconds
        self._fix_selector = fix_selector
        self._accounting = accounting

    @property
    def provider(self) -> AssistantProvider:
        """Public read-only access to the configured provider."""
        return self._provider

    def describe_models(self) -> AssistantModelsResponse:
        """Describe the models selectable within the fixed provider.

        Listing failures degrade to an empty, unreachable listing — they
        never surface as HTTP 5xx or raw provider errors.
        """
        provider_info = ProviderInfo(
            id=self._provider.provider_id, source=self._provider.source
        )
        try:
            listing = self._provider.list_models()
        except Exception:  # noqa: BLE001 - listing is best-effort by design
            listing = ModelListing(
                current_model="", available_models=[], reachable=False
            )
        return AssistantModelsResponse(
            provider=provider_info,
            current_model=listing.current_model,
            available_models=list(listing.available_models),
            reachable=listing.reachable,
        )

    def describe_health(self) -> AssistantHealthResponse:
        """Liveness envelope: process is up and a provider is configured."""
        return AssistantHealthResponse(
            status="ok",
            provider=ProviderInfo(
                id=self._provider.provider_id, source=self._provider.source
            ),
        )

    def handle(self, request: AssistantRequest) -> AssistantResponse:
        started_at = datetime.now(timezone.utc)

        if mode_requires_selected_text(request.mode) and not (
            request.selected_text or ""
        ).strip():
            response = AssistantResponse(
                request_id=request.request_id,
                mode=request.mode,
                status=AssistantStatus.UNCERTAINTY,
                result=SelectionRequiredResult().model_dump(),
                uncertainty=["No term was selected for translation."],
            )
            self._log_response(request, response, started_at)
            return response

        if (
            request.mode is AssistantMode.FIX_WORDING
            and self._fix_selector is not None
        ):
            return self._handle_fix_wording(request, started_at)

        response = _run_model_mode(
            request,
            provider=self._provider,
            accounting=self._accounting,
            max_tokens=self._max_tokens,
            timeout_seconds=self._timeout_seconds,
        )
        self._log_response(request, response, started_at)
        return response

    def _handle_fix_wording(
        self, request: AssistantRequest, started_at: datetime
    ) -> AssistantResponse:
        """Route ``fix_wording`` through the configured engine selector."""
        assert self._fix_selector is not None
        provider_info = ProviderInfo(
            id=self._provider.provider_id, source=self._provider.source
        )
        selector = self._fix_selector

        # Build the completion once — it is shared by any model-engine
        # invocation (direct or auto-fallback).
        completion = CompletionRequest(
            mode=AssistantMode.FIX_WORDING,
            system_prompt=build_system_prompt(AssistantMode.FIX_WORDING),
            user_prompt=build_user_prompt(request),
            max_tokens=self._max_tokens,
            timeout_seconds=self._timeout_seconds,
            model_override=request.model,
        )

        # --- engine-specific accounting lifecycle ---
        # Only the model path creates a provider accounting attempt.
        # LanguageTool uses not_applicable evidence (no model tokens).
        # Auto mode tries grammar first; on fallback, creates the attempt.
        attempt = None
        if selector.engine_mode == "model":
            attempt = begin_attempt(
                self._accounting, self._provider, completion, request.request_id
            )

        try:
            outcome = selector.run(
                request,
                max_tokens=self._max_tokens,
                timeout_seconds=self._timeout_seconds,
                accounting=self._accounting,
                _completion=completion,
            )
        except ProviderError as error:
            # Auto mode: grammar failed — create the model attempt now.
            if (
                attempt is None
                and selector.engine_mode == "auto"
                and selector.fallback == "model"
            ):
                attempt = begin_attempt(
                    self._accounting, self._provider, completion,
                    request.request_id,
                )
                try:
                    outcome = selector.run(
                        request,
                        max_tokens=self._max_tokens,
                        timeout_seconds=self._timeout_seconds,
                        accounting=self._accounting,
                        _completion=completion,
                    )
                except ProviderError as fallback_error:
                    response = self._failure_response(
                        request, provider_info, fallback_error,
                        accounting=self._finalize_failure_with_reply(
                            attempt, fallback_error
                        ),
                    )
                    self._log_response(request, response, started_at)
                    return response
            else:
                response = self._failure_response(
                    request, provider_info, error,
                    accounting=self._finalize_failure_with_reply(
                        attempt, error
                    ),
                )
                self._log_response(request, response, started_at)
                return response
        except Exception:  # pragma: no cover - defensive boundary
            response = self._failure_response(
                request,
                provider_info,
                ProviderError(
                    PublicErrorCategory.INTERNAL,
                    "The assistant request failed.",
                ),
                accounting=finalize_failure(attempt),
            )
            self._log_response(request, response, started_at)
            return response

        # Success: finalize with the provider reply from the engine.
        reply = outcome._provider_reply
        evidence = (
            finalize_reply(attempt, reply)
            if attempt is not None and reply is not None
            else outcome.accounting
        )
        result = outcome.result.model_dump()
        uncertainty = [
            str(item) for item in result.get("uncertainty", []) if str(item).strip()
        ]
        status = (
            AssistantStatus.UNCERTAINTY if uncertainty else AssistantStatus.COMPLETED
        )
        response = AssistantResponse(
            request_id=request.request_id,
            mode=request.mode,
            status=status,
            result=result,
            provider=outcome.provider,
            usage=outcome.usage,
            engine=outcome.engine,
            fallback_used=outcome.fallback_used,
            fallback_reason=outcome.fallback_reason,
            uncertainty=uncertainty,
            accounting=evidence,
        )
        self._log_response(request, response, started_at)
        return response

    @staticmethod
    def _finalize_failure_with_reply(
        attempt: UsageAttempt | None,
        error: ProviderError,
    ) -> UsageAccountingEvidence | None:
        """Finalize a failed attempt, preserving provider usage metadata
        when the error carries a reply (e.g. malformed response body)."""
        if attempt is None:
            return None
        reply = getattr(error, "_provider_reply", None)
        if reply is not None:
            return finalize_reply(attempt, reply)
        return finalize_failure(attempt)

    # Module-level helpers keep the class within its size baseline.
    _failure_response = staticmethod(_failure)
    _log_response = staticmethod(_log)
