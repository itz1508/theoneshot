"""Unit tests: native Anthropic provider adapter.

Covers status-code normalization, Messages-response parsing, usage
mapping, per-request model override, and dropdown listing — all without
network access (``requests.post`` is monkeypatched).
"""
from __future__ import annotations

import json

import pytest

from audisor_assistant.domain.modes import AssistantMode
from audisor_assistant.providers.anthropic import (
    ANTHROPIC_VERSION,
    DEFAULT_ANTHROPIC_BASE_URL,
    DEFAULT_ANTHROPIC_MODEL_CHOICES,
    CloudAnthropicProvider,
)
from audisor_assistant.providers.base import CompletionRequest, ProviderError
from audisor_assistant.providers.cloud import CLOUD_MODEL_CHOICES_VAR
from audisor_assistant.schemas.responses import PublicErrorCategory


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None) -> None:
        self.status_code = status_code
        self._payload = payload or {}

    def json(self) -> dict:
        return self._payload


def _completion_request(model_override: str | None = None) -> CompletionRequest:
    return CompletionRequest(
        mode=AssistantMode.EXPAND_IDEA,
        system_prompt="system",
        user_prompt="user",
        max_tokens=64,
        timeout_seconds=5.0,
        model_override=model_override,
    )


def _provider(monkeypatch) -> CloudAnthropicProvider:
    monkeypatch.setenv("AUDISOR_ASSISTANT_CLOUD_API_KEY", "test-key")
    return CloudAnthropicProvider(model_id="claude-test")


def _success_payload(text: str = '{"ok": true}') -> dict:
    return {
        "content": [{"type": "text", "text": text}],
        "usage": {"input_tokens": 7, "output_tokens": 3},
    }


def test_missing_model_is_configuration_error(monkeypatch):
    monkeypatch.delenv("AUDISOR_ASSISTANT_CLOUD_MODEL_ID", raising=False)
    with pytest.raises(ProviderError) as excinfo:
        CloudAnthropicProvider()
    assert excinfo.value.category is PublicErrorCategory.CONFIGURATION


def test_missing_api_key_is_configuration_error(monkeypatch):
    monkeypatch.delenv("AUDISOR_ASSISTANT_CLOUD_API_KEY", raising=False)
    provider = CloudAnthropicProvider(model_id="claude-test")
    with pytest.raises(ProviderError) as excinfo:
        provider.complete(_completion_request())
    assert excinfo.value.category is PublicErrorCategory.CONFIGURATION


@pytest.mark.parametrize(
    "status,category",
    [
        (401, PublicErrorCategory.AUTHENTICATION),
        (403, PublicErrorCategory.AUTHENTICATION),
        (429, PublicErrorCategory.RATE_LIMITED),
        (500, PublicErrorCategory.UNAVAILABLE),
        (503, PublicErrorCategory.UNAVAILABLE),
        (404, PublicErrorCategory.INVALID_RESPONSE),
    ],
)
def test_status_codes_normalize(monkeypatch, status, category):
    provider = _provider(monkeypatch)
    monkeypatch.setattr(
        "audisor_assistant.providers.anthropic.requests.post",
        lambda *a, **k: _FakeResponse(status),
    )
    with pytest.raises(ProviderError) as excinfo:
        provider.complete(_completion_request())
    assert excinfo.value.category is category


def test_success_parses_text_and_usage(monkeypatch):
    provider = _provider(monkeypatch)
    captured: dict = {}

    def _post(url, json=None, headers=None, timeout=None):
        captured.update(url=url, body=json, headers=headers, timeout=timeout)
        return _FakeResponse(200, _success_payload())

    monkeypatch.setattr(
        "audisor_assistant.providers.anthropic.requests.post", _post
    )
    reply = provider.complete(_completion_request())
    assert reply.text == '{"ok": true}'
    assert reply.usage == {
        "prompt_tokens": 7,
        "completion_tokens": 3,
        "total_tokens": 10,
    }
    assert captured["url"] == f"{DEFAULT_ANTHROPIC_BASE_URL}/v1/messages"
    assert captured["headers"]["x-api-key"] == "test-key"
    assert captured["headers"]["anthropic-version"] == ANTHROPIC_VERSION
    assert captured["body"]["system"] == "system"
    assert captured["body"]["messages"] == [{"role": "user", "content": "user"}]


def test_model_override_reaches_request_body(monkeypatch):
    provider = _provider(monkeypatch)
    captured: dict = {}

    def _post(url, json=None, headers=None, timeout=None):
        captured["model"] = json["model"]
        return _FakeResponse(200, _success_payload())

    monkeypatch.setattr(
        "audisor_assistant.providers.anthropic.requests.post", _post
    )
    provider.complete(_completion_request(model_override="claude-override"))
    assert captured["model"] == "claude-override"
    provider.complete(_completion_request())
    assert captured["model"] == "claude-test"


def test_empty_text_is_invalid_response(monkeypatch):
    provider = _provider(monkeypatch)
    monkeypatch.setattr(
        "audisor_assistant.providers.anthropic.requests.post",
        lambda *a, **k: _FakeResponse(200, {"content": []}),
    )
    with pytest.raises(ProviderError) as excinfo:
        provider.complete(_completion_request())
    assert excinfo.value.category is PublicErrorCategory.INVALID_RESPONSE


def test_malformed_payload_is_invalid_response(monkeypatch):
    provider = _provider(monkeypatch)
    monkeypatch.setattr(
        "audisor_assistant.providers.anthropic.requests.post",
        lambda *a, **k: _FakeResponse(200, {"unexpected": True}),
    )
    with pytest.raises(ProviderError) as excinfo:
        provider.complete(_completion_request())
    assert excinfo.value.category is PublicErrorCategory.INVALID_RESPONSE


def test_list_models_defaults(monkeypatch):
    monkeypatch.delenv(CLOUD_MODEL_CHOICES_VAR, raising=False)
    provider = CloudAnthropicProvider(model_id="claude-test")
    listing = provider.list_models()
    assert listing.current_model == "claude-test"
    assert listing.available_models == DEFAULT_ANTHROPIC_MODEL_CHOICES
    assert listing.reachable is None


def test_list_models_env_override(monkeypatch):
    monkeypatch.setenv(CLOUD_MODEL_CHOICES_VAR, "m-one, m-two ,")
    provider = CloudAnthropicProvider(model_id="claude-test")
    listing = provider.list_models()
    assert listing.available_models == ["m-one", "m-two"]


def test_no_credential_in_reply(monkeypatch):
    provider = _provider(monkeypatch)
    monkeypatch.setattr(
        "audisor_assistant.providers.anthropic.requests.post",
        lambda *a, **k: _FakeResponse(200, _success_payload()),
    )
    reply = provider.complete(_completion_request())
    assert "test-key" not in json.dumps(reply.text)
