from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from audisor.audisor_lifecycle.management import create_root_cause_issue
from audisor_assistant.application.service import AssistantService
from audisor_assistant.auth.development import DEV_IDENTITY_HEADER, ENVIRONMENT_VAR
from audisor_assistant.main import create_app
from audisor_assistant.providers.base import DeterministicFakeProvider


HEADERS = {DEV_IDENTITY_HEADER: "operator"}


@pytest.fixture()
def client(tmp_path: Path, monkeypatch) -> TestClient:
    monkeypatch.setenv(ENVIRONMENT_VAR, "development")
    monkeypatch.setenv("AUDISOR_STATE_ROOT", str(tmp_path / "aflow-state"))
    monkeypatch.setenv("AUDISOR_CONFIG_PATH", str(tmp_path / "config.json"))
    monkeypatch.delenv("AUDISOR_AFLOW_FALLBACK_PROVIDER", raising=False)
    from audisor_assistant.api import operations_routes

    operations_routes._controller = None
    operations_routes._event_store = None
    root = tmp_path / "aflow-state"
    trigger = {
        "artifact_id": "artifact.api",
        "artifact_type": "plan",
        "status": "draft_complete",
        "content": "private artifact content",
        "intent": "exercise management API",
        "context": "external MCP",
    }
    result = {
        "status": "error",
        "stage": "gap_finding",
        "detail": "Selected provider request timed out",
        "issue_code": "provider_timeout",
        "artifact_id": "artifact.api",
        "artifact_revision": 1,
        "lifecycle_run_id": "run-api",
        "submission_digest": "b" * 64,
        "completed_at": "2026-07-29T00:00:00+00:00",
    }
    create_root_cause_issue(root, result, trigger, [])
    app = create_app(AssistantService(DeterministicFakeProvider()))
    return TestClient(app)


def test_status_and_issue_reads_are_authenticated_and_redacted(client: TestClient) -> None:
    assert client.get("/v1/aflow/status").status_code == 401
    status = client.get("/v1/aflow/status", headers=HEADERS)
    assert status.status_code == 200
    assert status.json()["primary"]["provider"] == "local-openai-compatible"

    page = client.get("/v1/aflow/issues", headers=HEADERS)
    assert page.status_code == 200
    assert page.json()["total"] == 1
    assert "private artifact content" not in page.text

    issue_id = page.json()["items"][0]["issue_id"]
    detail = client.get(f"/v1/aflow/issues/{issue_id}", headers=HEADERS)
    assert detail.status_code == 200
    assert detail.json()["explanation"]["underlying_cause"] == "I don't know."


def test_external_mcp_issue_retry_is_disabled_with_originating_client_instruction(client: TestClient) -> None:
    issue_id = client.get("/v1/aflow/issues", headers=HEADERS).json()["items"][0]["issue_id"]
    response = client.post(
        f"/v1/aflow/issues/{issue_id}/retry-local",
        headers=HEADERS,
        json={"operation_version": 0, "idempotency_key": "retry-key-123"},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["reason"] == "retry_not_owned_by_operation_controller"
    assert "originating MCP client" in response.json()["detail"]["instruction"]


def test_retry_schema_rejects_unknown_properties(client: TestClient) -> None:
    issue_id = client.get("/v1/aflow/issues", headers=HEADERS).json()["items"][0]["issue_id"]
    response = client.post(
        f"/v1/aflow/issues/{issue_id}/retry-local",
        headers=HEADERS,
        json={"operation_version": 0, "idempotency_key": "retry-key-123", "unknown": True},
    )
    assert response.status_code == 422
