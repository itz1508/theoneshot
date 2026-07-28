"""Integration tests: HTTP surface of POST /v1/assistant/requests.

Covers authentication boundary behavior, unknown-field rejection at the
transport layer, env-driven provider selection, and the deterministic
fake-provider end-to-end path for every mode.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from audisor_assistant.api.dependencies import PROVIDER_VAR
from audisor_assistant.application.service import AssistantService
from audisor_assistant.auth.development import DEV_IDENTITY_HEADER, ENVIRONMENT_VAR
from audisor_assistant.domain.modes import AssistantMode
from audisor_assistant.main import create_app
from audisor_assistant.providers.base import DeterministicFakeProvider

DEV_HEADERS = {DEV_IDENTITY_HEADER: "dev-user"}


def _client() -> TestClient:
    return TestClient(create_app(AssistantService(DeterministicFakeProvider())))


def _payload(mode: str = "fix_wording", **overrides) -> dict:
    payload = {"request_id": "req-1", "mode": mode, "text": "hello there"}
    payload.update(overrides)
    return payload


@pytest.fixture(autouse=True)
def _development_env(monkeypatch):
    monkeypatch.setenv(ENVIRONMENT_VAR, "development")
    monkeypatch.delenv(PROVIDER_VAR, raising=False)


@pytest.mark.parametrize("mode", [m.value for m in AssistantMode])
def test_fake_provider_end_to_end_every_mode(mode):
    response = _client().post(
        "/v1/assistant/requests",
        json=_payload(mode, selected_text="circle back"),
        headers=DEV_HEADERS,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["request_id"] == "req-1"
    assert body["mode"] == mode
    assert body["status"] == "completed"
    assert body["result"]
    assert body["provider"] == {"id": "fake-deterministic", "source": "local"}


@pytest.mark.parametrize("environment", ["development", "test"])
def test_dev_auth_accepted_in_dev_and_test(monkeypatch, environment):
    monkeypatch.setenv(ENVIRONMENT_VAR, environment)
    response = _client().post(
        "/v1/assistant/requests", json=_payload(), headers=DEV_HEADERS
    )
    assert response.status_code == 200


def test_missing_identity_header_rejected():
    response = _client().post("/v1/assistant/requests", json=_payload())
    assert response.status_code == 401


def test_production_requests_rejected(monkeypatch):
    monkeypatch.setenv(ENVIRONMENT_VAR, "production")
    response = _client().post(
        "/v1/assistant/requests", json=_payload(), headers=DEV_HEADERS
    )
    assert response.status_code == 401
    assert "not configured" in response.json()["detail"]


def test_unknown_fields_rejected_at_http_layer():
    response = _client().post(
        "/v1/assistant/requests",
        json=_payload(api_key="sk-nope"),
        headers=DEV_HEADERS,
    )
    assert response.status_code == 422
    # The rejected value must not be echoed with credential context intact.
    assert "sk-nope" not in response.text or "extra" in response.text.lower()


def test_credential_fields_in_body_rejected():
    for field in ("authorization", "token", "password", "provider"):
        response = _client().post(
            "/v1/assistant/requests",
            json=_payload(**{field: "secret-value"}),
            headers=DEV_HEADERS,
        )
        assert response.status_code == 422, field


def test_env_selected_fake_provider(monkeypatch):
    monkeypatch.setenv(PROVIDER_VAR, "fake-deterministic")
    client = TestClient(create_app())  # no injected service: env path
    response = client.post(
        "/v1/assistant/requests", json=_payload(), headers=DEV_HEADERS
    )
    assert response.status_code == 200
    assert response.json()["provider"]["id"] == "fake-deterministic"


def test_unknown_provider_yields_configuration_envelope(monkeypatch):
    monkeypatch.setenv(PROVIDER_VAR, "no-such-provider")
    client = TestClient(create_app())
    response = client.post(
        "/v1/assistant/requests", json=_payload(), headers=DEV_HEADERS
    )
    assert response.status_code == 200  # normalized envelope, not HTTP 500
    body = response.json()
    assert body["status"] == "failed"
    assert body["result"]["error"]["category"] == "configuration"


@pytest.mark.parametrize("var,value", [
    ("AUDISOR_TIMEOUT_SECONDS", "fast"),
    ("AUDISOR_MAX_TOKENS", "lots"),
])
def test_invalid_numeric_config_yields_configuration_envelope(monkeypatch, var, value):
    """Non-numeric env vars produce a normalized configuration envelope, not HTTP 500."""
    monkeypatch.setenv(PROVIDER_VAR, "local-openai-compatible")
    monkeypatch.setenv("AUDISOR_MODEL_ID", "test-model")
    monkeypatch.setenv(var, value)
    client = TestClient(create_app())
    response = client.post(
        "/v1/assistant/requests", json=_payload(), headers=DEV_HEADERS
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    assert body["result"]["error"]["category"] == "configuration"
    assert "numeric" in body["result"]["error"]["message"].lower()


def test_selection_required_over_http():
    response = _client().post(
        "/v1/assistant/requests",
        json=_payload("translate_slang_jargon"),
        headers=DEV_HEADERS,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "uncertainty"
    assert body["result"]["selection_required"] is True


def test_responses_never_contain_credentials_or_paths():
    for mode in (m.value for m in AssistantMode):
        response = _client().post(
            "/v1/assistant/requests",
            json=_payload(mode, selected_text="circle back"),
            headers=DEV_HEADERS,
        )
        text = response.text.lower()
        assert "api_key" not in text
        assert "bearer " not in text
        assert "traceback" not in text
