"""Unit tests: service behavior — modes, gating, provider errors, no fallback."""
from __future__ import annotations

import json

import pytest

from audisor_assistant.application.service import (
    AssistantService,
    build_system_prompt,
)
from audisor_assistant.domain.modes import AssistantMode
from audisor_assistant.providers.base import (
    CompletionReply,
    CompletionRequest,
    DeterministicFakeProvider,
    ProviderCapabilities,
    ProviderError,
)
from audisor_assistant.schemas.requests import AssistantRequest
from audisor_assistant.schemas.responses import AssistantStatus, PublicErrorCategory


class _RecordingProvider:
    """Stub provider that records calls and replies or raises on demand."""

    provider_id = "stub"
    source = "local"
    capabilities = ProviderCapabilities()

    def __init__(self, reply: CompletionReply | None = None, error: ProviderError | None = None):
        self.calls: list[CompletionRequest] = []
        self._reply = reply
        self._error = error

    def complete(self, request: CompletionRequest) -> CompletionReply:
        self.calls.append(request)
        if self._error is not None:
            raise self._error
        assert self._reply is not None
        return self._reply


def _request(mode: str, **overrides) -> AssistantRequest:
    payload = {"request_id": "req-1", "mode": mode, "text": "hello there"}
    payload.update(overrides)
    return AssistantRequest.model_validate(payload)


@pytest.mark.parametrize("mode", [m.value for m in AssistantMode])
def test_every_mode_completes_with_fake_provider(mode):
    service = AssistantService(DeterministicFakeProvider())
    request = _request(mode, selected_text="circle back")
    response = service.handle(request)
    assert response.status is AssistantStatus.COMPLETED
    assert response.result
    assert response.provider is not None
    assert response.provider.id == "fake-deterministic"
    assert response.request_id == "req-1"


def test_fix_wording_prompt_preserves_tone():
    prompt = build_system_prompt(AssistantMode.FIX_WORDING)
    assert "Preserve tone" in prompt
    assert "Do not polish" in prompt
    assert "intentional_possible" in prompt


def test_message_improvement_fake_result_is_derived_from_input():
    response = AssistantService(DeterministicFakeProvider()).handle(
        _request("fix_wording", text="please help me explain this idea")
    )
    assert response.status is AssistantStatus.COMPLETED
    assert response.result["corrected_text"] == "please help me explain this idea"
    assert response.result["inferred_intent"]


def test_translate_without_selected_text_returns_selection_required():
    provider = _RecordingProvider()
    service = AssistantService(provider)
    response = service.handle(_request("translate_slang_jargon"))
    assert response.status is AssistantStatus.UNCERTAINTY
    assert response.result["selection_required"] is True
    # The provider must never be asked to pick a term silently.
    assert provider.calls == []


def test_malformed_provider_response_is_invalid_response():
    provider = _RecordingProvider(reply=CompletionReply(text="not json at all"))
    service = AssistantService(provider)
    response = service.handle(_request("fix_wording"))
    assert response.status is AssistantStatus.FAILED
    assert response.result["error"]["category"] == "invalid_response"


def test_contract_violating_provider_json_is_invalid_response():
    provider = _RecordingProvider(
        reply=CompletionReply(text=json.dumps({"unexpected": "shape"}))
    )
    service = AssistantService(provider)
    response = service.handle(_request("fix_wording"))
    assert response.status is AssistantStatus.FAILED
    assert response.result["error"]["category"] == "invalid_response"


@pytest.mark.parametrize(
    "category",
    [
        PublicErrorCategory.TIMEOUT,
        PublicErrorCategory.UNAVAILABLE,
        PublicErrorCategory.AUTHENTICATION,
        PublicErrorCategory.RATE_LIMITED,
        PublicErrorCategory.CONFIGURATION,
    ],
)
def test_provider_errors_normalized(category):
    provider = _RecordingProvider(error=ProviderError(category, "Provider failed."))
    service = AssistantService(provider)
    response = service.handle(_request("fix_wording"))
    assert response.status is AssistantStatus.FAILED
    assert response.result["error"]["category"] == category.value


def test_no_silent_fallback_single_provider_call():
    provider = _RecordingProvider(
        error=ProviderError(PublicErrorCategory.UNAVAILABLE, "down")
    )
    service = AssistantService(provider)
    response = service.handle(_request("fix_wording"))
    assert response.status is AssistantStatus.FAILED
    # Exactly one attempt against the configured provider; no fallback.
    assert len(provider.calls) == 1
    assert response.provider is not None
    assert response.provider.id == "stub"


def test_error_messages_are_sanitized():
    provider = _RecordingProvider(
        error=ProviderError(
            PublicErrorCategory.INTERNAL,
            "failed with Bearer sk-secret at C:\\keys\\k.txt",
        )
    )
    service = AssistantService(provider)
    response = service.handle(_request("fix_wording"))
    serialized = response.model_dump_json()
    assert "sk-secret" not in serialized
    assert "C:\\keys" not in serialized


def test_visualize_design_diagram_is_sanitized():
    payload = {
        "kind": "workflow",
        "diagram_code": "flowchart TD\n  A --> B\n  click A \"javascript:x()\"",
        "summary": "s",
        "builder_prompt": "b",
        "warnings": [],
    }
    provider = DeterministicFakeProvider(
        overrides={AssistantMode.VISUALIZE_DESIGN: payload}
    )
    service = AssistantService(provider)
    response = service.handle(_request("visualize_design"))
    assert response.status is AssistantStatus.COMPLETED
    assert "click" not in response.result["diagram_code"]
    assert response.warnings


def test_result_uncertainty_promotes_status():
    payload = {
        "expanded_text": "t",
        "preserved_intent": "i",
        "added_assumptions": [],
        "uncertainty": ["The audience is unclear."],
    }
    provider = DeterministicFakeProvider(overrides={AssistantMode.EXPAND_IDEA: payload})
    service = AssistantService(provider)
    response = service.handle(_request("expand_idea"))
    assert response.status is AssistantStatus.UNCERTAINTY
    assert response.uncertainty == ["The audience is unclear."]


def test_logs_never_contain_user_text_or_credentials(caplog):
    service = AssistantService(DeterministicFakeProvider())
    secret_text = "my SECRET-USER-TEXT with token sk-classified"
    with caplog.at_level("INFO", logger="audisor_assistant"):
        service.handle(_request("fix_wording", text=secret_text))
    logged = " ".join(record.getMessage() for record in caplog.records)
    assert logged  # the sanitized record was logged
    assert "SECRET-USER-TEXT" not in logged
    assert "sk-classified" not in logged
    assert "req-1" in logged
