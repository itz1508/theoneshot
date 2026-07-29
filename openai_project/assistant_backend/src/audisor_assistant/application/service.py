"""Assistant application service.

Orchestrates one request: mode gating, prompt construction, a single
provider call (no silent fallback), contract validation of the provider
output, sanitization, and the public response envelope.
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

logger = logging.getLogger("audisor_assistant")

_JSON_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)

_MODE_INSTRUCTIONS: dict[AssistantMode, str] = {
    AssistantMode.FIX_WORDING: (
        "Improve the user's full message, not just isolated words. Correct grammar "
        "and wording, clarify the likely intent, and preserve the user's voice. "
        "Preserve tone, rhythm, sentence fragments, and intentional informality. "
        "Do not polish wording that is already correct. "
        "Do not invent requirements or facts. Explain actual wording changes, then "
        "state the inferred intent, tone, context, assumptions, and uncertainty. "
        'Respond with only a JSON object: {"corrected_text": string, '
        '"changes": [{"original": string, "correction": string, "reason": string, '
        '"intentional_possible": boolean}], "no_changes_needed": boolean, '
        '"inferred_intent": string, "tone": string, "context": string, '
        '"assumptions": [string], "uncertainty": [string]}.'
    ),
    AssistantMode.DRAFT_THREE_REPLIES: (
        "Identify the central point of the message and what response is "
        "expected, then draft three usable replies: brief (roughly 2-3 "
        "sentences), thorough (addresses all material points), and diplomatic "
        "(careful and sensitive). If context is insufficient, state the "
        "limitation in the uncertainty list. "
        'Respond with only a JSON object: {"in_short": string, "brief": string, '
        '"thorough": string, "diplomatic": string, "message_purpose": string, '
        '"tone": string, "uncertainty": [string]}.'
    ),
    AssistantMode.TRANSLATE_SLANG_JARGON: (
        "Translate the selected slang or jargon term. Explain its meaning and "
        "social/emotional connotation, who commonly uses it, when the original "
        "is appropriate, and when the professional version is safer. If origin "
        "or usage is unclear, say so in usage_notes. "
        'Respond with only a JSON object: {"term": string, '
        '"professional_translation": string, "plain_meaning": string, '
        '"origin_context": string, "usage_notes": [string], '
        '"example": {"original": string, "professional": string}}.'
    ),
    AssistantMode.TEACH_CLEARLY: (
        "Teach the topic starting with prerequisites and the simplest "
        "explanation, progressing one concept at a time with concrete examples "
        "and analogies, including key insights and common misconceptions, and "
        "ending with a self-check. "
        'Respond with only a JSON object: {"basics": string, '
        '"building_from_there": [string], "key_insights": [string], '
        '"common_misconceptions": [string], "why_this_matters": string, '
        '"check_your_understanding": [string]}.'
    ),
    AssistantMode.EXPAND_IDEA: (
        "Expand the idea while remaining close to the user's meaning. Do not "
        "invent facts, names, commitments, or requirements. List every "
        "assumption you added rather than hiding it. "
        'Respond with only a JSON object: {"expanded_text": string, '
        '"preserved_intent": string, "added_assumptions": [string], '
        '"uncertainty": [string]}.'
    ),
    AssistantMode.VISUALIZE_DESIGN: (
        "First classify the user's description as exactly one kind: "
        "'layout' (a UI screen, page, or component arrangement), 'workflow' "
        "(a process, data flow, or system architecture), or 'unclear' (not "
        "enough concrete detail to draw anything). Use only components the "
        "user described or clearly implied; do not invent architecture. "
        "For 'layout': return collapsed (a short ASCII-tree overview, one "
        "string per line) and expanded (a detailed ASCII tree, one string "
        "per line); leave diagram_code null. For 'workflow': return Mermaid "
        "code in diagram_code with plain-text labels; leave collapsed and "
        "expanded null. For 'unclear': explain in summary exactly what is "
        "missing; leave the other fields null. Also return a builder prompt "
        "that reflects only the user's description (null when unclear). "
        'Respond with only a JSON object: {"kind": "layout"|"workflow"|'
        '"unclear", "summary": string, "collapsed": [string]|null, '
        '"expanded": [string]|null, "diagram_code": string|null, '
        '"builder_prompt": string|null, "warnings": [string]}.'
    ),
}

_SYSTEM_PREAMBLE = (
    "You are the Audisor Writing & Design Assistant. Respond with a single "
    "JSON object and nothing else. Never include markdown fences, "
    "commentary, credentials, or file paths."
)


def build_system_prompt(mode: AssistantMode) -> str:
    return f"{_SYSTEM_PREAMBLE}\n\n{_MODE_INSTRUCTIONS[mode]}"


def build_user_prompt(request: AssistantRequest) -> str:
    parts = [f"TEXT:\n{request.text}"]
    if request.selected_text:
        parts.append(f"SELECTED TERM:\n{request.selected_text}")
    if request.context:
        parts.append(f"CONTEXT:\n{request.context}")
    if request.tone:
        parts.append(f"REQUESTED TONE:\n{request.tone}")
    return "\n\n".join(parts)


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


class AssistantService:
    def __init__(
        self,
        provider: AssistantProvider,
        *,
        max_tokens: int = 0,
        timeout_seconds: float = 0.0,
        fix_selector: "FixEngineSelector | None" = None,
    ) -> None:
        self._provider = provider
        self._max_tokens = max_tokens
        self._timeout_seconds = timeout_seconds
        self._fix_selector = fix_selector

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
        provider_info = ProviderInfo(
            id=self._provider.provider_id, source=self._provider.source
        )

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

        try:
            reply = self._provider.complete(
                CompletionRequest(
                    mode=request.mode,
                    system_prompt=build_system_prompt(request.mode),
                    user_prompt=build_user_prompt(request),
                    max_tokens=self._max_tokens,
                    timeout_seconds=self._timeout_seconds,
                    model_override=request.model,
                )
            )
        except ProviderError as error:
            response = self._failure_response(request, provider_info, error)
            self._log_response(request, response, started_at)
            return response
        except Exception:  # pragma: no cover - defensive boundary
            response = self._failure_response(
                request,
                provider_info,
                ProviderError(
                    PublicErrorCategory.INTERNAL, "The assistant request failed."
                ),
            )
            self._log_response(request, response, started_at)
            return response

        try:
            payload = _extract_json(reply.text)
            result_model = MODE_RESULT_MODELS[request.mode].model_validate(payload)
        except (ValueError, ValidationError):
            response = self._failure_response(
                request,
                provider_info,
                ProviderError(
                    PublicErrorCategory.INVALID_RESPONSE,
                    "Provider returned a response that does not match the "
                    "mode contract.",
                ),
                usage=reply.usage,
            )
            self._log_response(request, response, started_at)
            return response

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
        response = AssistantResponse(
            request_id=request.request_id,
            mode=request.mode,
            status=status,
            result=result,
            warnings=warnings,
            provider=provider_info,
            usage=reply.usage,
            engine=engine,
            uncertainty=uncertainty,
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
        try:
            outcome = self._fix_selector.run(
                request,
                max_tokens=self._max_tokens,
                timeout_seconds=self._timeout_seconds,
            )
        except ProviderError as error:
            response = self._failure_response(request, provider_info, error)
            self._log_response(request, response, started_at)
            return response
        except Exception:  # pragma: no cover - defensive boundary
            response = self._failure_response(
                request,
                provider_info,
                ProviderError(
                    PublicErrorCategory.INTERNAL, "The assistant request failed."
                ),
            )
            self._log_response(request, response, started_at)
            return response

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
        )
        self._log_response(request, response, started_at)
        return response

    # Module-level helpers keep the class within its size baseline.
    _failure_response = staticmethod(_failure)
    _log_response = staticmethod(_log)
