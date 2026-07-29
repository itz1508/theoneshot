"""Unit tests: sanitized native Anthropic usage passthrough.

Covers the native-usage whitelist (cache categories preserved, unknown
categories stay absent — never zero), malformed-usage detection, the
frozen effective model on the reply, and the ruled source-selection
order (native → normalizer, absent → labelled compatibility fallback,
malformed → explicit ``native_usage_invalid`` with no silent fallback).
"""
from __future__ import annotations

import pytest

from audisor_assistant.domain.modes import AssistantMode
from audisor_assistant.providers.anthropic import CloudAnthropicProvider
from audisor_assistant.providers.base import CompletionRequest
from audisor_assistant.usage.models import AccountingStatus, MeasurementSource
from audisor_assistant.usage.normalizer import select_reply_usage


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


def _reply_with_usage(monkeypatch, usage: object):
    provider = _provider(monkeypatch)
    payload: dict = {"content": [{"type": "text", "text": '{"ok": true}'}]}
    if usage is not None:
        payload["usage"] = usage
    monkeypatch.setattr(
        "audisor_assistant.providers.anthropic.requests.post",
        lambda *a, **k: _FakeResponse(200, payload),
    )
    return provider.complete(_completion_request())


def test_native_usage_preserves_cache_categories(monkeypatch):
    reply = _reply_with_usage(
        monkeypatch,
        {
            "input_tokens": 7,
            "output_tokens": 3,
            "cache_read_input_tokens": 5,
            "cache_creation_input_tokens": 2,
        },
    )
    assert reply.native_usage == {
        "input_tokens": 7,
        "output_tokens": 3,
        "cache_read_input_tokens": 5,
        "cache_creation_input_tokens": 2,
    }
    assert reply.native_usage_invalid is False
    # Compatibility values stay intact for current consumers.
    assert reply.usage == {
        "prompt_tokens": 7,
        "completion_tokens": 3,
        "total_tokens": 10,
    }


def test_unknown_cache_categories_stay_absent_never_zero(monkeypatch):
    reply = _reply_with_usage(
        monkeypatch, {"input_tokens": 7, "output_tokens": 3}
    )
    assert reply.native_usage == {"input_tokens": 7, "output_tokens": 3}
    assert "cache_read_input_tokens" not in reply.native_usage
    assert "cache_creation_input_tokens" not in reply.native_usage

    normalized, warnings = select_reply_usage(
        "cloud-anthropic",
        native_usage=reply.native_usage,
        native_usage_invalid=reply.native_usage_invalid,
        compat_usage=reply.usage,
    )
    assert warnings == ()
    assert normalized.cached_read_tokens is None
    assert normalized.cache_write_tokens is None


def test_native_usage_never_carries_non_usage_payload(monkeypatch):
    reply = _reply_with_usage(
        monkeypatch,
        {
            "input_tokens": 7,
            "output_tokens": 3,
            "service_tier": "standard",
            "request_id": "provider-internal",
        },
    )
    # Only whitelisted usage counters cross the boundary.
    assert set(reply.native_usage) <= {
        "input_tokens",
        "output_tokens",
        "cache_read_input_tokens",
        "cache_creation_input_tokens",
    }


@pytest.mark.parametrize(
    "usage",
    [
        {"input_tokens": -1, "output_tokens": 3},
        {"input_tokens": "7", "output_tokens": 3},
        {"input_tokens": True, "output_tokens": 3},
        {"input_tokens": 7, "output_tokens": 3, "cache_read_input_tokens": 1.5},
        {"output_tokens": 3},
        "not-an-object",
    ],
)
def test_malformed_native_usage_is_flagged(monkeypatch, usage):
    reply = _reply_with_usage(monkeypatch, usage)
    assert reply.native_usage is None
    assert reply.native_usage_invalid is True


def test_absent_usage_is_not_invalid(monkeypatch):
    reply = _reply_with_usage(monkeypatch, None)
    assert reply.native_usage is None
    assert reply.native_usage_invalid is False


def test_reply_model_is_frozen_at_provider_boundary(monkeypatch):
    provider = _provider(monkeypatch)
    monkeypatch.setattr(
        "audisor_assistant.providers.anthropic.requests.post",
        lambda *a, **k: _FakeResponse(
            200,
            {
                "content": [{"type": "text", "text": '{"ok": true}'}],
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        ),
    )
    assert provider.complete(_completion_request()).model == "claude-test"
    assert (
        provider.complete(_completion_request(model_override="claude-override")).model
        == "claude-override"
    )
    assert provider.effective_model("claude-override") == "claude-override"
    assert provider.effective_model(None) == "claude-test"


def test_valid_native_usage_uses_anthropic_normalizer():
    normalized, warnings = select_reply_usage(
        "cloud-anthropic",
        native_usage={
            "input_tokens": 7,
            "output_tokens": 3,
            "cache_read_input_tokens": 5,
            "cache_creation_input_tokens": 2,
        },
        native_usage_invalid=False,
        compat_usage={"prompt_tokens": 7, "completion_tokens": 3},
    )
    assert warnings == ()
    assert normalized.source is MeasurementSource.PROVIDER_REPORTED
    assert normalized.status is AccountingStatus.COMPLETE
    assert normalized.cached_read_tokens == 5
    assert normalized.cache_write_tokens == 2


def test_invalid_native_usage_never_falls_back_silently():
    normalized, warnings = select_reply_usage(
        "cloud-anthropic",
        native_usage=None,
        native_usage_invalid=True,
        compat_usage={"prompt_tokens": 7, "completion_tokens": 3},
    )
    assert warnings == ("native_usage_invalid",)
    assert normalized.source is MeasurementSource.UNAVAILABLE
    assert normalized.status is AccountingStatus.INCOMPLETE
    assert normalized.input_tokens is None


def test_absent_native_usage_uses_labelled_compatibility_fallback():
    normalized, warnings = select_reply_usage(
        "cloud-anthropic",
        native_usage=None,
        native_usage_invalid=False,
        compat_usage={
            "prompt_tokens": 7,
            "completion_tokens": 3,
            "total_tokens": 10,
        },
    )
    assert warnings == ("compatibility_usage_fallback",)
    assert normalized.source is MeasurementSource.PROVIDER_COMPATIBLE
    assert normalized.input_tokens == 7
    assert normalized.output_tokens == 3


def test_missing_usage_everywhere_is_reported():
    normalized, warnings = select_reply_usage(
        "cloud-anthropic",
        native_usage=None,
        native_usage_invalid=False,
        compat_usage=None,
    )
    assert warnings == ("provider_usage_missing",)
    assert normalized.status is AccountingStatus.INCOMPLETE
