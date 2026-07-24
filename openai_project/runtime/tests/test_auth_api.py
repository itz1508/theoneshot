"""Auth endpoint tombstone tests for 0.10.0."""

from __future__ import annotations

from fastapi.testclient import TestClient

from audisor.main import create_app


def test_register_returns_410() -> None:
    response = TestClient(create_app()).post(
        "/v1/auth/register", json={"username": "alice", "password": "secret"}
    )
    assert response.status_code == 410
    assert response.json()["code"] == "legacy_runtime_deprecated"


def test_login_returns_410() -> None:
    response = TestClient(create_app()).post(
        "/v1/auth/login", json={"username": "alice", "password": "secret"}
    )
    assert response.status_code == 410


def test_logout_returns_410() -> None:
    response = TestClient(create_app()).post("/v1/auth/logout")
    assert response.status_code == 410


def test_me_returns_410() -> None:
    response = TestClient(create_app()).get("/v1/auth/me")
    assert response.status_code == 410
