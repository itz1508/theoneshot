"""Integration tests: GET /v1/assistant/models, GET /v1/assistant/health,
per-request model override, kind-based visualize contract, and cloud
credential fail-fast at startup.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from audisor_assistant.api.dependencies import PROVIDER_VAR
from audisor_assistant.application.service import AssistantService
from audisor_assistant.auth.development import DEV_IDENTITY_HEADER, ENVIRONMENT_VAR
from audisor_assistant.domain.modes import AssistantMode
from audisor_assistant.main import CLOUD_API_KEY_VAR, create_app
from audisor_assistant.providers.base import (
    CompletionReply,
    CompletionRequest,
    DeterministicFakeProvider,
    ModelListing,
    ProviderCapabilities,
)

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
    monkeypatch.delenv(CLOUD_API_KEY_VAR, raising=False)


class _CapturingProvider:
    """Fake provider that records the CompletionRequest it received."""

    provider_id = "capturing"
    source = "local"
    capabilities = ProviderCapabilities()

    def __init__(self, reply_text: str) -> None:
        self._reply_text = reply_text
        self.seen: list[CompletionRequest] = []

    def complete(self, request: CompletionRequest) -> CompletionReply:
        self.seen.append(request)
        return CompletionReply(text=self._reply_text, usage=None)

    def list_models(self) -> ModelListing:
        return ModelListing(
            current_model="cap-default",
            available_models=["cap-default", "cap-alt"],
            reachable=True,
        )


# ---------------------------------------------------------------- models


def test_models_endpoint_returns_listing():
    response = _client().get("/v1/assistant/models", headers=DEV_HEADERS)
    assert response.status_code == 200
    body = response.json()
    assert body["provider"] == {"id": "fake-deterministic", "source": "local"}
    assert body["current_model"] == "fake-deterministic"
    assert body["available_models"] == ["fake-deterministic"]
    assert body["reachable"] is True


def test_models_endpoint_requires_auth():
    assert _client().get("/v1/assistant/models").status_code == 401


def test_models_endpoint_misconfigured_provider_degrades(monkeypatch):
    monkeypatch.setenv(PROVIDER_VAR, "fake-deterministic")
    client = TestClient(create_app())
    monkeypatch.setenv(PROVIDER_VAR, "no-such-provider")
    response = client.get("/v1/assistant/models", headers=DEV_HEADERS)
    assert response.status_code == 200  # never HTTP 5xx
    body = response.json()
    assert body["provider"]["id"] == "unconfigured"
    assert body["available_models"] == []
    assert body["reachable"] is False


def test_models_response_carries_no_credentials(monkeypatch):
    monkeypatch.setenv("AUDISOR_ASSISTANT_CLOUD_API_KEY", "sk-secret")
    response = _client().get("/v1/assistant/models", headers=DEV_HEADERS)
    assert "sk-secret" not in response.text


# ---------------------------------------------------------------- health


def test_health_endpoint_is_unauthenticated():
    response = _client().get("/v1/assistant/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["provider"] == {"id": "fake-deterministic", "source": "local"}


# ---------------------------------------------------------- model override


def test_model_override_threads_to_provider():
    provider = _CapturingProvider('{"expanded_text": "x", "preserved_intent": "y"}')
    client = TestClient(create_app(AssistantService(provider)))
    response = client.post(
        "/v1/assistant/requests",
        json=_payload("expand_idea", model="alt-model:7b"),
        headers=DEV_HEADERS,
    )
    assert response.status_code == 200
    assert provider.seen[0].model_override == "alt-model:7b"


def test_model_absent_means_no_override():
    provider = _CapturingProvider('{"expanded_text": "x", "preserved_intent": "y"}')
    client = TestClient(create_app(AssistantService(provider)))
    client.post(
        "/v1/assistant/requests", json=_payload("expand_idea"), headers=DEV_HEADERS
    )
    assert provider.seen[0].model_override is None


@pytest.mark.parametrize("bad_model", ["", "model name", "model$«x»", "a" * 201])
def test_invalid_model_values_rejected(bad_model):
    response = _client().post(
        "/v1/assistant/requests",
        json=_payload(model=bad_model),
        headers=DEV_HEADERS,
    )
    assert response.status_code == 422


def test_provider_field_still_rejected():
    """The model field must not reopen client-side provider selection."""
    response = _client().post(
        "/v1/assistant/requests",
        json=_payload(provider="cloud-anthropic"),
        headers=DEV_HEADERS,
    )
    assert response.status_code == 422


# ------------------------------------------------------- visualize kinds


def _visualize_client(payload: dict) -> TestClient:
    provider = DeterministicFakeProvider(
        overrides={AssistantMode.VISUALIZE_DESIGN: payload}
    )
    return TestClient(create_app(AssistantService(provider)))


def test_visualize_layout_kind():
    client = _visualize_client(
        {
            "kind": "layout",
            "summary": "A settings page.",
            "collapsed": ["Page", "- Header"],
            "expanded": ["Page", "- Header", "-- Title"],
            "diagram_code": None,
            "builder_prompt": "Build the settings page.",
            "warnings": [],
        }
    )
    response = client.post(
        "/v1/assistant/requests", json=_payload("visualize_design"), headers=DEV_HEADERS
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["result"]["kind"] == "layout"
    assert body["result"]["collapsed"] == ["Page", "- Header"]
    assert body["result"]["diagram_code"] is None


def test_visualize_workflow_kind_is_sanitized():
    client = _visualize_client(
        {
            "kind": "workflow",
            "summary": "A flow.",
            "diagram_code": 'flowchart TD\n  A --> B\n  click A "javascript:x()"',
            "builder_prompt": "Build the flow.",
            "warnings": [],
        }
    )
    response = client.post(
        "/v1/assistant/requests", json=_payload("visualize_design"), headers=DEV_HEADERS
    )
    body = response.json()
    assert body["result"]["kind"] == "workflow"
    assert "click" not in body["result"]["diagram_code"]
    assert body["warnings"]


def test_visualize_unclear_kind():
    client = _visualize_client(
        {"kind": "unclear", "summary": "Describe the components involved."}
    )
    response = client.post(
        "/v1/assistant/requests", json=_payload("visualize_design"), headers=DEV_HEADERS
    )
    body = response.json()
    assert body["status"] == "completed"
    assert body["result"]["kind"] == "unclear"
    assert body["result"]["diagram_code"] is None


def test_visualize_unknown_kind_fails_contract():
    client = _visualize_client({"kind": "mindmap", "summary": "nope"})
    response = client.post(
        "/v1/assistant/requests", json=_payload("visualize_design"), headers=DEV_HEADERS
    )
    body = response.json()
    assert body["status"] == "failed"
    assert body["result"]["error"]["category"] == "invalid_response"


# ------------------------------------------------------------- fail-fast


@pytest.mark.parametrize("provider_id", ["cloud-openai-compatible", "cloud-anthropic"])
def test_cloud_provider_without_key_fails_startup(monkeypatch, provider_id):
    monkeypatch.setenv(PROVIDER_VAR, provider_id)
    with pytest.raises(RuntimeError) as excinfo:
        create_app()
    assert CLOUD_API_KEY_VAR in str(excinfo.value)


def test_cloud_provider_with_key_starts(monkeypatch):
    monkeypatch.setenv(PROVIDER_VAR, "cloud-anthropic")
    monkeypatch.setenv(CLOUD_API_KEY_VAR, "test-key")
    app = create_app()
    assert app is not None


@pytest.mark.parametrize("provider_id", ["fake-deterministic", "local-openai-compatible"])
def test_non_cloud_providers_start_without_key(monkeypatch, provider_id):
    monkeypatch.setenv(PROVIDER_VAR, provider_id)
    app = create_app()
    assert app is not None


def test_injected_service_bypasses_fail_fast(monkeypatch):
    monkeypatch.setenv(PROVIDER_VAR, "cloud-anthropic")
    app = create_app(AssistantService(DeterministicFakeProvider()))
    assert app is not None
