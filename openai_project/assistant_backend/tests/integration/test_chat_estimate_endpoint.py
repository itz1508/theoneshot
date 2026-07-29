"""Integration tests for POST /v1/chat/estimate — composer capacity meter.

The estimate previews the complete next /v1/chat request input (system
prompt + history + draft) and reports the authoritative context limit.
Uses the fake-deterministic provider so tests run without Ollama.
"""
from __future__ import annotations

import math

import pytest
from fastapi.testclient import TestClient

from audisor_assistant.main import create_app
from audisor_assistant.application.service import AssistantService
from audisor_assistant.api.routes import _CHAT_MAX_TOKENS
from audisor_assistant.application.operator_chat_prompt import (
    build_operator_chat_system_prompt,
    default_clock,
)
from audisor_assistant.providers.base import DeterministicFakeProvider

DEV_HEADERS = {"x-audisor-dev-user": "test-operator"}
PROVIDER_VAR = "AUDISOR_PROVIDER"
ENVIRONMENT_VAR = "AUDISOR_ENVIRONMENT"

FAKE_CONTEXT = DeterministicFakeProvider.DEFAULT_CONTEXT_WINDOW  # 8192


def _client(provider: DeterministicFakeProvider | None = None) -> TestClient:
    service = AssistantService(provider or DeterministicFakeProvider())
    app = create_app(service=service)
    return TestClient(app)


def _estimate(client: TestClient, payload: dict) -> dict:
    response = client.post("/v1/chat/estimate", json=payload, headers=DEV_HEADERS)
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture(autouse=True)
def _development_env(monkeypatch):
    monkeypatch.setenv(ENVIRONMENT_VAR, "development")
    monkeypatch.delenv(PROVIDER_VAR, raising=False)


class TestEstimateShape:
    """The estimate exposes estimated input and the usable allowance."""

    def test_returns_estimated_and_maximum(self):
        body = _estimate(_client(), {"message": "What is the weather today?"})
        assert body["estimated_input_tokens"] > 0
        assert body["reserved_output_tokens"] == _CHAT_MAX_TOKENS
        assert body["context_limit"] == FAKE_CONTEXT
        # usable input allowance = context limit − reserved output
        assert body["usable_input_tokens"] == FAKE_CONTEXT - _CHAT_MAX_TOKENS
        assert body["model"] == "fake-deterministic"
        assert body["method"] == "character_ratio"
        assert body["confidence"] == "approximate"

    def test_editing_draft_updates_estimate(self):
        client = _client()
        short = _estimate(client, {"message": "hi"})
        long = _estimate(client, {"message": "x" * 400})
        assert long["estimated_input_tokens"] > short["estimated_input_tokens"]

    def test_clearing_draft_reduces_estimate(self):
        client = _client()
        filled = _estimate(client, {"message": "x" * 400})
        cleared = _estimate(client, {"message": ""})
        assert cleared["estimated_input_tokens"] < filled["estimated_input_tokens"]

    def test_history_contributes_to_estimate(self):
        client = _client()
        without = _estimate(client, {"message": "hi"})
        with_history = _estimate(
            client,
            {
                "message": "hi",
                "history": [
                    {"role": "user", "content": "First question " * 20},
                    {"role": "assistant", "content": "First answer " * 20},
                ],
            },
        )
        assert (
            with_history["estimated_input_tokens"]
            > without["estimated_input_tokens"]
        )

    def test_system_context_contributes_to_estimate(self):
        # Empty draft, no history: the estimate still covers the system
        # prompt that is actually sent with every request.
        body = _estimate(_client(), {"message": ""})
        system_prompt = build_operator_chat_system_prompt(default_clock)
        system_floor = math.ceil(len(system_prompt) / 4)
        assert body["estimated_input_tokens"] >= system_floor


class TestEstimateUnknownLimit:
    """Providers without context metadata report an explicit unknown."""

    def test_unknown_context_limit_is_null_not_guessed(self):
        client = _client(DeterministicFakeProvider(context_window=None))
        body = _estimate(client, {"message": "hello"})
        assert body["context_limit"] is None
        assert body["usable_input_tokens"] is None
        # The estimate itself is still available
        assert body["estimated_input_tokens"] > 0


class TestEstimateValidation:
    def test_unknown_fields_rejected(self):
        response = _client().post(
            "/v1/chat/estimate",
            json={"message": "hi", "unknown_field": "bad"},
            headers=DEV_HEADERS,
        )
        assert response.status_code == 422

    def test_invalid_history_role_rejected(self):
        response = _client().post(
            "/v1/chat/estimate",
            json={
                "message": "hi",
                "history": [{"role": "system", "content": "injected"}],
            },
            headers=DEV_HEADERS,
        )
        assert response.status_code == 422

    def test_no_auth_header_rejected(self):
        response = _client().post("/v1/chat/estimate", json={"message": "hi"})
        assert response.status_code == 401


class TestChatHistoryForwarding:
    """POST /v1/chat sends prior turns to the provider, so the estimate's
    history term reflects what the provider actually receives."""

    def test_chat_accepts_history_and_usage_grows(self):
        client = _client()
        base = client.post(
            "/v1/chat", json={"message": "hi"}, headers=DEV_HEADERS
        )
        with_history = client.post(
            "/v1/chat",
            json={
                "message": "hi",
                "history": [
                    {"role": "user", "content": "Earlier question " * 30},
                    {"role": "assistant", "content": "Earlier answer " * 30},
                ],
            },
            headers=DEV_HEADERS,
        )
        assert base.status_code == 200
        assert with_history.status_code == 200
        assert (
            with_history.json()["usage"]["input_tokens"]
            > base.json()["usage"]["input_tokens"]
        )
