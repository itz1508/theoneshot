"""Auth endpoint tests."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from audisor.main import create_app


@pytest.fixture
def client(tmp_path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("AUDISOR_DATA_DIR", str(tmp_path))
    return TestClient(create_app())


def test_register_and_login(client: TestClient) -> None:
    r = client.post("/v1/auth/register", json={"username": "alice", "password": "secret123"})
    assert r.status_code == 200
    assert r.json() == {"username": "alice"}

    r = client.post("/v1/auth/login", json={"username": "alice", "password": "secret123"})
    assert r.status_code == 200
    token = r.json()["access_token"]
    assert r.json()["token_type"] == "bearer"

    r = client.get("/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json() == {"username": "alice"}


def test_login_wrong_password(client: TestClient) -> None:
    client.post("/v1/auth/register", json={"username": "bob", "password": "rightpass"})
    r = client.post("/v1/auth/login", json={"username": "bob", "password": "wrongpass"})
    assert r.status_code == 401
    assert "Invalid" in r.json()["detail"]


def test_register_duplicate(client: TestClient) -> None:
    client.post("/v1/auth/register", json={"username": "carol", "password": "secret123"})
    r = client.post("/v1/auth/register", json={"username": "carol", "password": "otherpass"})
    assert r.status_code == 409
    assert "already exists" in r.json()["detail"]


def test_me_without_token(client: TestClient) -> None:
    r = client.get("/v1/auth/me")
    assert r.status_code == 401


def test_logout_invalidates_token(client: TestClient) -> None:
    client.post("/v1/auth/register", json={"username": "dave", "password": "secret123"})
    r = client.post("/v1/auth/login", json={"username": "dave", "password": "secret123"})
    token = r.json()["access_token"]

    r = client.post("/v1/auth/logout", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 204

    r = client.get("/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401