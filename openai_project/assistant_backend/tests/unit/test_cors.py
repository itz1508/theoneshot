"""CORS behaviour: opt-in, exact origins only, fail-fast configuration.

Default (no AUDISOR_CORS_ORIGINS) must add no CORS headers at all so
same-origin and Vite-proxy behaviour stay untouched.  Configured origins
are matched exactly; wildcards and malformed entries abort startup.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from audisor_assistant.application.service import AssistantService
from audisor_assistant.auth.development import DEV_IDENTITY_HEADER
from audisor_assistant.main import CORS_ORIGINS_VAR, create_app, parse_cors_origins
from audisor_assistant.providers.base import DeterministicFakeProvider

ALLOWED = "http://localhost:5173"
OTHER = "https://app.example.com"
DENIED = "https://evil.example.com"

DEV_HEADERS = {DEV_IDENTITY_HEADER: "dev-user"}


def _client() -> TestClient:
    return TestClient(create_app(AssistantService(DeterministicFakeProvider())))


def _payload() -> dict:
    return {"request_id": "cors-1", "mode": "fix_wording", "text": "teh cat"}


def _post(client: TestClient, origin: str | None = None):
    headers = dict(DEV_HEADERS)
    if origin is not None:
        headers["Origin"] = origin
    return client.post("/v1/assistant/requests", json=_payload(), headers=headers)


def _preflight(client: TestClient, origin: str):
    return client.options(
        "/v1/assistant/requests",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,x-audisor-dev-user",
        },
    )


class TestParseCorsOrigins:
    def test_none_and_empty_disable_cors(self):
        assert parse_cors_origins(None) == []
        assert parse_cors_origins("") == []
        assert parse_cors_origins("  , ,") == []

    def test_single_origin(self):
        assert parse_cors_origins(ALLOWED) == [ALLOWED]

    def test_multiple_origins_trimmed_and_deduplicated(self):
        raw = f" {ALLOWED} , {OTHER}, {ALLOWED} "
        assert parse_cors_origins(raw) == [ALLOWED, OTHER]

    def test_mixed_case_duplicate_collapses_to_one(self):
        raw = f"{ALLOWED},HTTP://LOCALHOST:5173"
        assert parse_cors_origins(raw) == [ALLOWED]

    def test_wildcard_rejected(self):
        with pytest.raises(RuntimeError, match="wildcard"):
            parse_cors_origins("*")
        with pytest.raises(RuntimeError, match="wildcard"):
            parse_cors_origins("https://*.example.com")

    @pytest.mark.parametrize(
        "entry",
        [
            "https://x.example.com/path",
            "ftp://x.example.com",
            "localhost:5173",
            "garbage",
            "https://user:pw@x.example.com",
        ],
    )
    def test_malformed_entry_rejected_and_named(self, entry: str):
        with pytest.raises(RuntimeError, match="malformed origin") as excinfo:
            parse_cors_origins(f"{ALLOWED},{entry}")
        assert entry in str(excinfo.value)


class TestCorsDisabledByDefault:
    def test_no_env_var_no_acao_header(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv(CORS_ORIGINS_VAR, raising=False)
        response = _post(_client(), origin=ALLOWED)
        assert response.status_code == 200
        assert "access-control-allow-origin" not in response.headers

    def test_no_env_var_preflight_not_handled_by_cors(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.delenv(CORS_ORIGINS_VAR, raising=False)
        response = _preflight(_client(), ALLOWED)
        assert "access-control-allow-origin" not in response.headers


class TestCorsConfigured:
    def test_single_allowed_origin_echoed(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv(CORS_ORIGINS_VAR, ALLOWED)
        response = _post(_client(), origin=ALLOWED)
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == ALLOWED

    def test_multiple_allowed_origins(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv(CORS_ORIGINS_VAR, f"{ALLOWED},{OTHER}")
        client = _client()
        for origin in (ALLOWED, OTHER):
            response = _post(client, origin=origin)
            assert response.headers["access-control-allow-origin"] == origin

    def test_allowed_preflight(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv(CORS_ORIGINS_VAR, ALLOWED)
        response = _preflight(_client(), ALLOWED)
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == ALLOWED
        assert "POST" in response.headers["access-control-allow-methods"]
        allowed_headers = response.headers["access-control-allow-headers"].lower()
        assert "content-type" in allowed_headers
        assert "x-audisor-dev-user" in allowed_headers

    def test_denied_origin_gets_no_acao(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv(CORS_ORIGINS_VAR, ALLOWED)
        response = _post(_client(), origin=DENIED)
        assert "access-control-allow-origin" not in response.headers

    def test_denied_origin_preflight_rejected(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv(CORS_ORIGINS_VAR, ALLOWED)
        response = _preflight(_client(), DENIED)
        assert response.status_code == 400
        assert "access-control-allow-origin" not in response.headers

    def test_credentials_never_allowed(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv(CORS_ORIGINS_VAR, ALLOWED)
        client = _client()
        assert "access-control-allow-credentials" not in _post(
            client, origin=ALLOWED
        ).headers
        assert "access-control-allow-credentials" not in _preflight(
            client, ALLOWED
        ).headers

    def test_malformed_configuration_aborts_startup(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv(CORS_ORIGINS_VAR, "https://ok.example.com,*")
        with pytest.raises(RuntimeError, match="wildcard"):
            create_app(AssistantService(DeterministicFakeProvider()))

    def test_same_origin_request_unaffected(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv(CORS_ORIGINS_VAR, ALLOWED)
        response = _post(_client())  # no Origin header at all
        assert response.status_code == 200
        assert response.json()["provider"]["id"] == "fake-deterministic"
