"""Unit tests: fix-wording engine boundary (Phase G).

Covers the engine selector matrix, match application, provenance
ownership (envelope vs. result), and the G11 consistency invariant:
envelope ``engine`` and result ``result_kind`` never disagree.
"""
from __future__ import annotations

import json

import pytest

from audisor_assistant.application.fix_engines import (
    FIX_ENGINE_VAR,
    FIX_FALLBACK_VAR,
    FixEngineSelector,
    GrammarEngineState,
    GrammarMatch,
    LanguageToolFixEngine,
    ModelFixEngine,
    _apply_matches,
    build_grammar_state,
)
from audisor_assistant.application.service import AssistantService
from audisor_assistant.providers.base import (
    CompletionReply,
    CompletionRequest,
    ProviderCapabilities,
    ProviderError,
)
from audisor_assistant.schemas.requests import AssistantRequest
from audisor_assistant.schemas.responses import AssistantStatus, PublicErrorCategory


class _StubProvider:
    provider_id = "stub"
    source = "local"
    capabilities = ProviderCapabilities()

    def __init__(self, payload: dict | None = None, error: ProviderError | None = None):
        self.calls: list[CompletionRequest] = []
        self._payload = payload
        self._error = error

    def complete(self, request: CompletionRequest) -> CompletionReply:
        self.calls.append(request)
        if self._error is not None:
            raise self._error
        return CompletionReply(
            text=json.dumps(self._payload), usage={"total_tokens": 7}
        )


class _FakeChecker:
    """GrammarChecker fake: canned matches or a forced failure."""

    def __init__(self, matches: list[GrammarMatch] | None = None, fail: bool = False):
        self.matches = matches or []
        self.fail = fail
        self.calls: list[str] = []

    def check(self, text: str) -> list[GrammarMatch]:
        self.calls.append(text)
        if self.fail:
            raise RuntimeError("checker exploded")
        return self.matches


_MODEL_PAYLOAD = {
    "corrected_text": "Hello there.",
    "changes": [],
    "no_changes_needed": True,
    "inferred_intent": "greeting",
    "tone": "casual",
    "context": "",
    "assumptions": [],
    "uncertainty": [],
}


def _request(text: str = "helo there") -> AssistantRequest:
    return AssistantRequest.model_validate(
        {"request_id": "req-fx", "mode": "fix_wording", "text": text}
    )


def _run(selector: FixEngineSelector):
    return selector.run(_request(), max_tokens=0, timeout_seconds=0.0)


# --- _apply_matches -------------------------------------------------------


def test_apply_matches_applies_non_overlapping_first_replacements():
    text = "Teh cat sat on teh mat"
    matches = [
        GrammarMatch(offset=0, length=3, rule_id="R1", message="typo",
                     replacements=("The",)),
        GrammarMatch(offset=15, length=3, rule_id="R1", message="typo",
                     replacements=("the", "that")),
    ]
    corrected, changes = _apply_matches(text, matches)
    assert corrected == "The cat sat on the mat"
    assert [c.correction for c in changes] == ["The", "the"]
    assert changes[0].offset == 0 and changes[0].length == 3
    assert changes[1].rule_id == "R1"
    assert changes[1].replacements == ["the", "that"]


def test_apply_matches_keeps_overlapping_and_empty_replacement_matches_visible():
    text = "abcdef"
    matches = [
        GrammarMatch(offset=0, length=4, rule_id="A", message="m1",
                     replacements=("wxyz",)),
        # Overlaps the first match — must not be applied, but stays listed.
        GrammarMatch(offset=2, length=2, rule_id="B", message="m2",
                     replacements=("zz",)),
        # No replacement offered — represented with correction == original.
        GrammarMatch(offset=5, length=1, rule_id="C", message="m3"),
    ]
    corrected, changes = _apply_matches(text, matches)
    assert corrected == "wxyzef"
    assert len(changes) == 3
    assert changes[1].correction == changes[1].original == "cd"
    assert changes[2].correction == changes[2].original == "f"


# --- engines --------------------------------------------------------------


def test_model_engine_forces_model_result_kind():
    payload = dict(_MODEL_PAYLOAD, result_kind="languagetool")
    outcome = ModelFixEngine(_StubProvider(payload)).run(
        _request(), max_tokens=0, timeout_seconds=0.0
    )
    assert outcome.result.result_kind == "model"
    assert outcome.engine == "model"
    assert outcome.provider.id == "stub"
    assert outcome.usage == {"total_tokens": 7}
    assert outcome.fallback_used is False


def test_model_engine_invalid_json_raises_invalid_response():
    provider = _StubProvider()
    provider._payload = None  # complete() will serialize None -> "null"
    with pytest.raises(ProviderError) as excinfo:
        ModelFixEngine(provider).run(_request(), max_tokens=0, timeout_seconds=0.0)
    assert excinfo.value.category is PublicErrorCategory.INVALID_RESPONSE


def test_languagetool_engine_result_and_provenance():
    checker = _FakeChecker(
        [GrammarMatch(offset=0, length=4, rule_id="TYPO", message="typo",
                      replacements=("Hello",))]
    )
    outcome = LanguageToolFixEngine(checker).run(
        _request("helo there"), max_tokens=0, timeout_seconds=0.0
    )
    assert outcome.result.result_kind == "languagetool"
    assert outcome.result.corrected_text == "Hello there"
    assert outcome.result.no_changes_needed is False
    assert outcome.engine == "languagetool"
    assert outcome.provider.id == "languagetool"
    assert outcome.provider.source == "local"
    assert outcome.usage is None


def test_languagetool_engine_clean_text_reports_no_changes_needed():
    outcome = LanguageToolFixEngine(_FakeChecker()).run(
        _request("Hello there."), max_tokens=0, timeout_seconds=0.0
    )
    assert outcome.result.no_changes_needed is True
    assert outcome.result.changes == []


def test_languagetool_engine_checker_failure_is_unavailable():
    with pytest.raises(ProviderError) as excinfo:
        LanguageToolFixEngine(_FakeChecker(fail=True)).run(
            _request(), max_tokens=0, timeout_seconds=0.0
        )
    assert excinfo.value.category is PublicErrorCategory.UNAVAILABLE


# --- selector matrix ------------------------------------------------------


def _grammar_state(checker: _FakeChecker | None = None, error: str = "",
                   category: PublicErrorCategory = PublicErrorCategory.CONFIGURATION
                   ) -> GrammarEngineState:
    if checker is not None:
        return GrammarEngineState(engine=LanguageToolFixEngine(checker))
    return GrammarEngineState(error=error, error_category=category)


def test_selector_default_is_model_and_never_touches_grammar(monkeypatch):
    monkeypatch.delenv(FIX_ENGINE_VAR, raising=False)
    monkeypatch.delenv(FIX_FALLBACK_VAR, raising=False)
    checker = _FakeChecker()
    selector = FixEngineSelector.from_env(
        ModelFixEngine(_StubProvider(_MODEL_PAYLOAD)), _grammar_state(checker)
    )
    outcome = _run(selector)
    assert outcome.engine == "model"
    assert checker.calls == []


def test_selector_languagetool_uses_grammar_engine(monkeypatch):
    monkeypatch.setenv(FIX_ENGINE_VAR, "languagetool")
    provider = _StubProvider(_MODEL_PAYLOAD)
    selector = FixEngineSelector.from_env(
        ModelFixEngine(provider), _grammar_state(_FakeChecker())
    )
    outcome = _run(selector)
    assert outcome.engine == "languagetool"
    assert provider.calls == []


def test_selector_languagetool_unavailable_ignores_fallback(monkeypatch):
    monkeypatch.setenv(FIX_ENGINE_VAR, "languagetool")
    monkeypatch.setenv(FIX_FALLBACK_VAR, "model")
    selector = FixEngineSelector.from_env(
        ModelFixEngine(_StubProvider(_MODEL_PAYLOAD)),
        _grammar_state(error="not installed"),
    )
    with pytest.raises(ProviderError) as excinfo:
        _run(selector)
    assert excinfo.value.category is PublicErrorCategory.CONFIGURATION


def test_selector_auto_without_fallback_raises_when_grammar_missing(monkeypatch):
    monkeypatch.setenv(FIX_ENGINE_VAR, "auto")
    monkeypatch.setenv(FIX_FALLBACK_VAR, "none")
    selector = FixEngineSelector.from_env(
        ModelFixEngine(_StubProvider(_MODEL_PAYLOAD)),
        _grammar_state(error="down", category=PublicErrorCategory.UNAVAILABLE),
    )
    with pytest.raises(ProviderError) as excinfo:
        _run(selector)
    assert excinfo.value.category is PublicErrorCategory.UNAVAILABLE


def test_selector_auto_falls_back_to_model_with_provenance(monkeypatch):
    monkeypatch.setenv(FIX_ENGINE_VAR, "auto")
    monkeypatch.setenv(FIX_FALLBACK_VAR, "model")
    selector = FixEngineSelector.from_env(
        ModelFixEngine(_StubProvider(_MODEL_PAYLOAD)),
        _grammar_state(error="grammar init failed",
                       category=PublicErrorCategory.UNAVAILABLE),
    )
    outcome = _run(selector)
    assert outcome.engine == "model"
    assert outcome.fallback_used is True
    assert outcome.fallback_reason == "grammar init failed"
    assert outcome.result.result_kind == "model"


def test_selector_auto_falls_back_on_runtime_checker_failure(monkeypatch):
    monkeypatch.setenv(FIX_ENGINE_VAR, "auto")
    monkeypatch.setenv(FIX_FALLBACK_VAR, "model")
    selector = FixEngineSelector.from_env(
        ModelFixEngine(_StubProvider(_MODEL_PAYLOAD)),
        _grammar_state(_FakeChecker(fail=True)),
    )
    outcome = _run(selector)
    assert outcome.fallback_used is True
    assert outcome.fallback_reason == "The grammar checker is unavailable."


@pytest.mark.parametrize(
    ("engine", "fallback"),
    [("turbo", "none"), ("model", "sometimes"), ("", "invalid")],
)
def test_selector_unknown_env_values_are_configuration_errors(
    monkeypatch, engine, fallback
):
    # Empty strings fall back to defaults, so only set non-empty invalids.
    if engine:
        monkeypatch.setenv(FIX_ENGINE_VAR, engine)
    else:
        monkeypatch.delenv(FIX_ENGINE_VAR, raising=False)
    monkeypatch.setenv(FIX_FALLBACK_VAR, fallback)
    selector = FixEngineSelector.from_env(
        ModelFixEngine(_StubProvider(_MODEL_PAYLOAD)), _grammar_state(_FakeChecker())
    )
    with pytest.raises(ProviderError) as excinfo:
        _run(selector)
    assert excinfo.value.category is PublicErrorCategory.CONFIGURATION


def test_build_grammar_state_model_mode_never_imports_languagetool():
    state = build_grammar_state("model")
    assert state.engine is None
    assert state.error == ""


def test_build_grammar_state_reports_missing_grammar_extra(monkeypatch):
    # Simulate an environment without the grammar extra: a None entry in
    # sys.modules makes the adapter import raise ImportError.
    import sys

    monkeypatch.setitem(
        sys.modules, "audisor_assistant.application.languagetool_adapter", None
    )
    state = build_grammar_state("languagetool")
    assert state.engine is None
    assert state.error
    assert state.error_category is PublicErrorCategory.CONFIGURATION


def test_build_grammar_state_reports_init_failure_as_unavailable(monkeypatch):
    adapter = pytest.importorskip(
        "audisor_assistant.application.languagetool_adapter"
    )

    def _boom():
        raise RuntimeError("no java")

    monkeypatch.setattr(adapter, "build_languagetool_checker", _boom)
    state = build_grammar_state("auto")
    assert state.engine is None
    assert state.error_category is PublicErrorCategory.UNAVAILABLE


# --- envelope <-> result consistency (G11) --------------------------------


def _service_with(selector_env: dict[str, str], monkeypatch,
                  checker: _FakeChecker | None = None) -> AssistantService:
    for var in (FIX_ENGINE_VAR, FIX_FALLBACK_VAR):
        monkeypatch.delenv(var, raising=False)
    for var, value in selector_env.items():
        monkeypatch.setenv(var, value)
    provider = _StubProvider(_MODEL_PAYLOAD)
    state = _grammar_state(checker) if checker else _grammar_state(
        error="down", category=PublicErrorCategory.UNAVAILABLE
    )
    selector = FixEngineSelector.from_env(ModelFixEngine(provider), state)
    return AssistantService(provider, fix_selector=selector)


def test_envelope_engine_matches_result_kind_model_path(monkeypatch):
    service = _service_with({FIX_ENGINE_VAR: "model"}, monkeypatch)
    response = service.handle(_request())
    assert response.status is AssistantStatus.COMPLETED
    assert response.engine == response.result["result_kind"] == "model"
    assert response.fallback_used is False
    assert response.provider.id == "stub"


def test_envelope_engine_matches_result_kind_languagetool_path(monkeypatch):
    service = _service_with(
        {FIX_ENGINE_VAR: "languagetool"}, monkeypatch, checker=_FakeChecker()
    )
    response = service.handle(_request())
    assert response.status is AssistantStatus.COMPLETED
    assert response.engine == response.result["result_kind"] == "languagetool"
    # provider.id is provider identity for the executing engine — never
    # a substitute for fallback metadata.
    assert response.provider.id == "languagetool"
    assert response.provider.source == "local"
    assert response.usage is None


def test_envelope_fallback_provenance_on_auto_path(monkeypatch):
    service = _service_with(
        {FIX_ENGINE_VAR: "auto", FIX_FALLBACK_VAR: "model"}, monkeypatch
    )
    response = service.handle(_request())
    assert response.status is AssistantStatus.COMPLETED
    assert response.engine == response.result["result_kind"] == "model"
    assert response.fallback_used is True
    assert response.fallback_reason == "down"
    # The real provider identity is preserved on the fallback path.
    assert response.provider.id == "stub"


def test_generic_path_without_selector_still_satisfies_invariant():
    provider = _StubProvider(dict(_MODEL_PAYLOAD, result_kind="languagetool"))
    response = AssistantService(provider).handle(_request())
    assert response.status is AssistantStatus.COMPLETED
    assert response.engine == response.result["result_kind"] == "model"


def test_selector_failure_produces_normalized_failed_envelope(monkeypatch):
    service = _service_with(
        {FIX_ENGINE_VAR: "auto", FIX_FALLBACK_VAR: "none"}, monkeypatch
    )
    response = service.handle(_request())
    assert response.status is AssistantStatus.FAILED
    assert response.result["error"]["category"] == "unavailable"
    assert response.engine is None
    assert response.fallback_used is False
