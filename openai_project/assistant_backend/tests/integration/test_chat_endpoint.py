"""Integration tests for POST /v1/chat — operator chat endpoint.

Uses the fake-deterministic provider so tests run without Ollama.
Validates real token usage is returned and error handling works.
"""
from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from audisor_assistant.main import create_app
from audisor_assistant.application.service import AssistantService
from audisor_assistant.providers.base import DeterministicFakeProvider

DEV_HEADERS = {"x-audisor-dev-user": "test-operator"}
PROVIDER_VAR = "AUDISOR_PROVIDER"
ENVIRONMENT_VAR = "AUDISOR_ENVIRONMENT"


def _client() -> TestClient:
    """Create a test client with the fake-deterministic provider."""
    service = AssistantService(DeterministicFakeProvider())
    app = create_app(service=service)
    return TestClient(app)


@pytest.fixture(autouse=True)
def _development_env(monkeypatch):
    monkeypatch.setenv(ENVIRONMENT_VAR, "development")
    monkeypatch.delenv(PROVIDER_VAR, raising=False)


class TestChatEndpointSuccess:
    """POST /v1/chat with valid input returns a real reply and real token counts."""

    def test_returns_200_with_reply_and_usage(self):
        response = _client().post(
            "/v1/chat",
            json={"message": "Hello, how are you today?"},
            headers=DEV_HEADERS,
        )
        assert response.status_code == 200
        body = response.json()

        # Reply is present and non-empty
        assert isinstance(body["reply"], str)
        assert len(body["reply"]) > 0

        # Provider identity is present
        assert body["provider"]["id"] == "fake-deterministic"
        assert body["provider"]["source"] == "local"

        # Model is present
        assert isinstance(body["model"], str)
        assert len(body["model"]) > 0

        # Usage has real counts > 0 (fake provider returns len-based)
        usage = body["usage"]
        assert usage["input_tokens"] > 0
        assert usage["output_tokens"] > 0
        assert usage["total_tokens"] == usage["input_tokens"] + usage["output_tokens"]
        assert usage["provider_type"] == "local"
        assert usage["cost"] is None  # local = no cost

    def test_long_message_returns_proportional_input_tokens(self):
        long_msg = "x" * 200
        response = _client().post(
            "/v1/chat",
            json={"message": long_msg},
            headers=DEV_HEADERS,
        )
        assert response.status_code == 200
        usage = response.json()["usage"]
        # ~200/4 = 50 tokens for input
        assert usage["input_tokens"] >= 50


class TestChatEndpointValidation:
    """POST /v1/chat rejects invalid requests with 422."""

    def test_empty_message_rejected(self):
        response = _client().post(
            "/v1/chat",
            json={"message": ""},
            headers=DEV_HEADERS,
        )
        assert response.status_code == 422

    def test_missing_message_rejected(self):
        response = _client().post(
            "/v1/chat",
            json={},
            headers=DEV_HEADERS,
        )
        assert response.status_code == 422

    def test_unknown_fields_rejected(self):
        response = _client().post(
            "/v1/chat",
            json={"message": "hi", "unknown_field": "bad"},
            headers=DEV_HEADERS,
        )
        assert response.status_code == 422


class TestChatEndpointAuth:
    """POST /v1/chat requires authentication."""

    def test_no_auth_header_rejected(self):
        response = _client().post(
            "/v1/chat",
            json={"message": "hi"},
        )
        assert response.status_code == 401


class TestWritingEndpointRejectsChat:
    """The writing assistant endpoint must not accept 'chat' as a mode."""

    def test_assistant_requests_rejects_chat_mode(self):
        response = _client().post(
            "/v1/assistant/requests",
            json={
                "request_id": "req-reject-chat",
                "mode": "chat",
                "selected_text": "hello",
            },
            headers=DEV_HEADERS,
        )
        assert response.status_code == 422
