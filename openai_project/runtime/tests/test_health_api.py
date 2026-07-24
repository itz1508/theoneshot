"""Health and readiness tombstone tests for 0.10.0."""

from __future__ import annotations

from fastapi.testclient import TestClient

from audisor.main import create_app


def test_health_returns_200_with_tombstone_body() -> None:
    response = TestClient(create_app()).get("/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "deprecated",
        "serving_mode": "tombstone",
        "removal_version": "1.0.0",
    }


def test_ready_returns_200_with_legacy_runtime_unavailable() -> None:
    response = TestClient(create_app()).get("/ready")
    assert response.status_code == 200
    assert response.json() == {
        "ready": True,
        "serving_mode": "tombstone",
        "legacy_runtime_available": False,
    }


def test_openapi_preserves_required_routes() -> None:
    document = create_app().openapi()
    assert {
        "/health",
        "/ready",
        "/v1/tasks",
        "/v1/builds/prepare",
        "/v1/builds/{build_id}/executions",
    }.issubset(document["paths"])
    assert document["info"]["version"] == "0.10.0"
