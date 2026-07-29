"""Fix-wording engine boundary.

Only ``fix_wording`` routes through this selector; the other five modes
always use the provider path in :class:`AssistantService`.

Engines
-------
- :class:`ModelFixEngine` — provider.complete -> JSON extraction ->
  contract validation -> domain result (``result_kind="model"``).
- :class:`LanguageToolFixEngine` — grammar-checker matches converted
  directly into a :class:`FixWordingResult` (``result_kind="languagetool"``);
  no model JSON path is involved.

Provenance ownership: the response envelope carries execution metadata
(``engine``, ``fallback_used``, ``fallback_reason``); the result carries
only its semantic shape (``result_kind``); ``provider.id`` stays provider
identity and never encodes fallback state.

Configuration (server-side env only, mirrored by run-dev.ps1):
- ``AUDISOR_FIX_ENGINE``: ``model`` (default) | ``languagetool`` | ``auto``
- ``AUDISOR_FIX_FALLBACK``: ``none`` (default) | ``model``
Unknown values yield a normalized configuration error for ``fix_wording``
requests only; the backend stays up and other modes are unaffected.
"""
from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from pydantic import ValidationError

from ..domain.modes import AssistantMode
from ..domain.results import FixWordingChange, FixWordingResult
from ..providers.base import AssistantProvider, CompletionReply, CompletionRequest, ProviderError
from ..schemas.requests import AssistantRequest
from ..schemas.responses import ProviderInfo, PublicErrorCategory
from ..usage.public import UsageAccountingEvidence
from .usage_integration import (
    UsageAccountingIntegration,
    not_applicable_evidence,
)

FIX_ENGINE_VAR = "AUDISOR_FIX_ENGINE"
FIX_FALLBACK_VAR = "AUDISOR_FIX_FALLBACK"

_VALID_ENGINES = {"model", "languagetool", "auto"}
_VALID_FALLBACKS = {"none", "model"}


@dataclass(frozen=True)
class GrammarMatch:
    """One grammar-checker finding, checker-neutral."""

    offset: int
    length: int
    rule_id: str
    message: str
    replacements: tuple[str, ...] = ()


@runtime_checkable
class GrammarChecker(Protocol):
    """Minimal grammar-checker surface (implemented by the LanguageTool
    adapter in the ``grammar`` extra, and by test fakes)."""

    def check(self, text: str) -> list[GrammarMatch]:
        ...


@dataclass(frozen=True)
class FixOutcome:
    """Successful engine execution plus its provenance."""

    result: FixWordingResult
    provider: ProviderInfo
    usage: dict[str, int] | None
    engine: str
    fallback_used: bool = False
    fallback_reason: str = ""
    accounting: UsageAccountingEvidence | None = None
    _provider_reply: CompletionReply | None = None


@dataclass
class GrammarEngineState:
    """Eager-init outcome for the grammar engine, resolved at startup so
    requests never trigger surprise downloads.  ``engine is None`` with an
    ``error`` means initialization failed or was not attempted."""

    engine: "LanguageToolFixEngine | None" = None
    error: str = ""
    error_category: PublicErrorCategory = PublicErrorCategory.CONFIGURATION


class ModelFixEngine:
    """Model path: single provider call, JSON extraction, validation."""

    engine_id = "model"

    def __init__(self, provider: AssistantProvider) -> None:
        self._provider = provider

    def run(
        self, request: AssistantRequest, *, max_tokens: int, timeout_seconds: float,
        accounting: UsageAccountingIntegration | None = None,
        _completion: CompletionRequest | None = None,
    ) -> FixOutcome:
        # Imported here to keep the dependency one-directional
        # (service never imports this module's internals).
        from .service import _extract_json, build_system_prompt, build_user_prompt

        if _completion is None:
            _completion = CompletionRequest(
                mode=AssistantMode.FIX_WORDING,
                system_prompt=build_system_prompt(AssistantMode.FIX_WORDING),
                user_prompt=build_user_prompt(request),
                max_tokens=max_tokens,
                timeout_seconds=timeout_seconds,
                model_override=request.model,
            )
        reply = self._provider.complete(_completion)
        try:
            payload = _extract_json(reply.text)
            result = FixWordingResult.model_validate(payload)
        except (ValueError, ValidationError) as error:
            # Attach the raw reply so the caller can preserve usage
            # metadata even though the response body is invalid.
            err = ProviderError(
                PublicErrorCategory.INVALID_RESPONSE,
                "Provider returned a response that does not match the "
                "mode contract.",
            )
            err._provider_reply = reply  # type: ignore[attr-defined]
            raise err from error
        if result.result_kind != "model":
            # Providers cannot claim another engine's semantic shape.
            result = result.model_copy(update={"result_kind": "model"})
        return FixOutcome(
            result=result,
            provider=ProviderInfo(
                id=self._provider.provider_id, source=self._provider.source
            ),
            usage=reply.usage,
            engine=self.engine_id,
            _provider_reply=reply,
        )


class LanguageToolFixEngine:
    """Grammar-checker path: matches become the result directly.

    Access to the underlying checker is serialized with a lock —
    language_tool_python wraps a single external process and is not
    proven thread-safe under FastAPI's sync threadpool.
    """

    engine_id = "languagetool"

    def __init__(self, checker: GrammarChecker) -> None:
        self._checker = checker
        self._lock = threading.Lock()

    def run(
        self, request: AssistantRequest, *, max_tokens: int, timeout_seconds: float,
        accounting: UsageAccountingIntegration | None = None,
    ) -> FixOutcome:
        text = request.text
        try:
            with self._lock:
                matches = self._checker.check(text)
        except Exception as error:
            raise ProviderError(
                PublicErrorCategory.UNAVAILABLE,
                "The grammar checker is unavailable.",
            ) from error

        corrected, changes = _apply_matches(text, matches)
        result = FixWordingResult(
            corrected_text=corrected,
            result_kind="languagetool",
            changes=changes,
            no_changes_needed=not matches,
        )
        return FixOutcome(
            result=result,
            provider=ProviderInfo(id="languagetool", source="local"),
            usage=None,
            engine=self.engine_id,
            accounting=not_applicable_evidence(
                accounting,
                operation_id=request.request_id,
                provider="languagetool",
            ),
        )


def _apply_matches(
    text: str, matches: list[GrammarMatch]
) -> tuple[str, list[FixWordingChange]]:
    """Build the corrected text and change list from checker matches.

    Non-overlapping matches with a replacement are applied left-to-right
    (first replacement wins).  Overlapping or replacement-less matches are
    still represented as changes (correction == original) so nothing the
    checker found is hidden.
    """
    ordered = sorted(matches, key=lambda m: (m.offset, m.length))
    pieces: list[str] = []
    changes: list[FixWordingChange] = []
    cursor = 0
    for match in ordered:
        original = text[match.offset : match.offset + match.length]
        applied = bool(match.replacements) and match.offset >= cursor
        correction = match.replacements[0] if match.replacements else original
        if applied:
            pieces.append(text[cursor : match.offset])
            pieces.append(correction)
            cursor = match.offset + match.length
        changes.append(
            FixWordingChange(
                original=original,
                correction=correction if applied else original,
                reason=match.message,
                offset=match.offset,
                length=match.length,
                rule_id=match.rule_id,
                replacements=list(match.replacements),
            )
        )
    pieces.append(text[cursor:])
    return "".join(pieces), changes


def build_grammar_state(engine_mode: str | None = None) -> GrammarEngineState:
    """Eagerly initialize the grammar engine per configuration.

    ``engine=model`` (or invalid config) never imports LanguageTool.
    Failure never prevents startup — it only disables ``fix_wording``
    per the fallback policy.
    """
    mode = (engine_mode or os.environ.get(FIX_ENGINE_VAR, "model")).strip()
    if mode not in ("languagetool", "auto"):
        return GrammarEngineState()
    try:
        from .languagetool_adapter import build_languagetool_checker
    except ImportError:
        return GrammarEngineState(
            error="The grammar extra (language-tool-python) is not installed.",
            error_category=PublicErrorCategory.CONFIGURATION,
        )
    try:
        checker = build_languagetool_checker()
    except Exception:
        return GrammarEngineState(
            error="The grammar checker failed to initialize.",
            error_category=PublicErrorCategory.UNAVAILABLE,
        )
    return GrammarEngineState(engine=LanguageToolFixEngine(checker))


@dataclass
class FixEngineSelector:
    """Chooses the engine for one ``fix_wording`` request per the
    configured matrix and records fallback provenance."""

    engine_mode: str
    fallback: str
    model_engine: ModelFixEngine
    grammar_state: GrammarEngineState = field(default_factory=GrammarEngineState)
    config_error: str = ""

    @classmethod
    def from_env(
        cls,
        model_engine: ModelFixEngine,
        grammar_state: GrammarEngineState | None = None,
    ) -> "FixEngineSelector":
        engine_mode = os.environ.get(FIX_ENGINE_VAR, "model").strip() or "model"
        fallback = os.environ.get(FIX_FALLBACK_VAR, "none").strip() or "none"
        config_error = ""
        if engine_mode not in _VALID_ENGINES:
            config_error = "Configured fix-wording engine is not supported."
        elif fallback not in _VALID_FALLBACKS:
            config_error = "Configured fix-wording fallback is not supported."
        return cls(
            engine_mode=engine_mode,
            fallback=fallback,
            model_engine=model_engine,
            grammar_state=grammar_state or GrammarEngineState(),
            config_error=config_error,
        )

    def run(
        self, request: AssistantRequest, *, max_tokens: int, timeout_seconds: float,
        accounting: UsageAccountingIntegration | None = None,
        _completion: CompletionRequest | None = None,
    ) -> FixOutcome:
        if self.config_error:
            raise ProviderError(PublicErrorCategory.CONFIGURATION, self.config_error)

        if self.engine_mode == "model":
            return self.model_engine.run(
                request, max_tokens=max_tokens,
                timeout_seconds=timeout_seconds, accounting=accounting,
                _completion=_completion,
            )

        grammar = self.grammar_state.engine
        if self.engine_mode == "languagetool":
            # Fallback is deliberately ignored for the explicit selection.
            if grammar is None:
                raise ProviderError(
                    self.grammar_state.error_category,
                    self.grammar_state.error or "The grammar checker is unavailable.",
                )
            return grammar.run(
                request, max_tokens=max_tokens,
                timeout_seconds=timeout_seconds, accounting=accounting,
            )

        # engine_mode == "auto"
        if grammar is not None:
            try:
                return grammar.run(
                    request, max_tokens=max_tokens,
                    timeout_seconds=timeout_seconds, accounting=accounting,
                )
            except ProviderError as error:
                if self.fallback != "model":
                    raise
                reason = error.public_message
        else:
            if self.fallback != "model":
                raise ProviderError(
                    self.grammar_state.error_category,
                    self.grammar_state.error or "The grammar checker is unavailable.",
                )
            reason = (
                self.grammar_state.error or "The grammar checker is unavailable."
            )

        outcome = self.model_engine.run(
            request, max_tokens=max_tokens,
            timeout_seconds=timeout_seconds, accounting=accounting,
            _completion=_completion,
        )
        return FixOutcome(
            result=outcome.result,
            provider=outcome.provider,
            usage=outcome.usage,
            engine=outcome.engine,
            fallback_used=True,
            fallback_reason=reason,
            accounting=outcome.accounting,
            _provider_reply=outcome._provider_reply,
        )
