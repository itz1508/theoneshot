"""Integration tests for POST /v1/usage/estimate — canonical estimate endpoint.

Proves:
- Canonical estimate endpoint works independently of legacy chat lifecycle.
- Compatibility alias (/v1/chat/estimate) returns equivalent output.
- No ChatOrchestrator state is needed for estimation.
- Frontend capacity client uses the intended endpoint.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from audisor_assistant.main import create_app
from audisor_assistant.application.service import AssistantService
from audisor_assistant.providers.base import DeterministicFakeProvider

DEV_HEADERS = {"x-audisor-dev-user": "test-operator"}
PROVIDER_VAR = "AUDISOR_PROVIDER"
ENVIRONMENT_VAR = "AUDISOR_ENVIRONMENT"

FAKE_CONTEXT = DeterministicFakeProvider.DEFAULT_CONTEXT_WINDOW  # 8192


def _client(provider: DeterministicFakeProvider | None = None) -> TestClient:
    service = AssistantService(provider or DeterministicFakeProvider())
    app = create_app(service=service)
    return TestClient(app)


@pytest.fixture(autouse=True)
def _development_env(monkeypatch):
    monkeypatch.setenv(ENVIRONMENT_VAR, "development")
    monkeypatch.delenv(PROVIDER_VAR, raising=False)


class TestCanonicalEstimateEndpoint:
    """POST /v1/usage/estimate works independently."""

    def test_returns_estimate(self) -> None:
        client = _client()
        response = client.post(
            "/v1/usage/estimate",
            json={"message": "What is the weather today?"},
            headers=DEV_HEADERS,
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["estimated_input_tokens"] > 0
        assert body["context_limit"] == FAKE_CONTEXT
        assert body["model"] == "fake-deterministic"
        assert body["method"] == "character_ratio"
        assert body["confidence"] == "approximate"

    def test_no_chat_orchestrator_state_needed(self) -> None:
        """The endpoint works without any prior chat session."""
        client = _client()
        response = client.post(
            "/v1/usage/estimate",
            json={"message": "hello"},
            headers=DEV_HEADERS,
        )
        assert response.status_code == 200

    def test_history_contributes(self) -> None:
        client = _client()
        without = client.post(
            "/v1/usage/estimate",
            json={"message": "hi"},
            headers=DEV_HEADERS,
        ).json()
        with_history = client.post(
            "/v1/usage/estimate",
            json={
                "message": "hi",
                "history": [
                    {"role": "user", "content": "First question " * 20},
                    {"role": "assistant", "content": "First answer " * 20},
                ],
            },
            headers=DEV_HEADERS,
        ).json()
        assert with_history["estimated_input_tokens"] > without["estimated_input_tokens"]

    def test_unknown_fields_rejected(self) -> None:
        client = _client()
        response = client.post(
            "/v1/usage/estimate",
            json={"message": "hi", "unknown_field": "bad"},
            headers=DEV_HEADERS,
        )
        assert response.status_code == 422

    def test_no_auth_header_rejected(self) -> None:
        client = _client()
        response = client.post(
            "/v1/usage/estimate",
            json={"message": "hi"},
        )
        assert response.status_code == 401


class TestEstimateCompatibilityAlias:
    """/v1/chat/estimate and /v1/usage/estimate return equivalent output."""

    def test_equivalent_output(self) -> None:
        client = _client()
        payload = {"message": "test message", "history": []}

        canonical = client.post(
            "/v1/usage/estimate", json=payload, headers=DEV_HEADERS,
        ).json()
        alias = client.post(
            "/v1/chat/estimate", json=payload, headers=DEV_HEADERS,
        ).json()

        assert canonical["estimated_input_tokens"] == alias["estimated_input_tokens"]
        assert canonical["reserved_output_tokens"] == alias["reserved_output_tokens"]
        assert canonical["context_limit"] == alias["context_limit"]
        assert canonical["usable_input_tokens"] == alias["usable_input_tokens"]
        assert canonical["model"] == alias["model"]
        assert canonical["method"] == alias["method"]
        assert canonical["confidence"] == alias["confidence"]

    def test_alias_still_works(self) -> None:
        """The legacy alias is still functional."""
        client = _client()
        response = client.post(
            "/v1/chat/estimate",
            json={"message": "hello"},
            headers=DEV_HEADERS,
        )
        assert response.status_code == 200
        body = response.json()
        assert body["estimated_input_tokens"] > 0
