"""Integration tests for /v1/operations endpoints.

Covers: create, events polling, resume, cancel, status, and replay protection.
Uses patched controller/event_store singletons backed by tmp_path.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from audisor_assistant.auth.development import DEV_IDENTITY_HEADER, ENVIRONMENT_VAR
from audisor_assistant.main import create_app
from audisor_assistant.application.service import AssistantService
from audisor_assistant.providers.base import DeterministicFakeProvider

DEV_HEADERS = {DEV_IDENTITY_HEADER: "operator"}


# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _development_env(monkeypatch):
    monkeypatch.setenv(ENVIRONMENT_VAR, "development")


@pytest.fixture()
def client(tmp_path: Path):
    """Create a TestClient with patched controller singletons using tmp_path."""
    from operation_controller.controller import FileOperationStore, OperationController
    from operation_controller.event_store import EventStore

    store_root = tmp_path / "ops"
    store = FileOperationStore(store_root)
    event_store = EventStore(store_root)
    controller = OperationController(store=store, event_store=event_store)

    app = create_app(AssistantService(DeterministicFakeProvider()))

    # Patch the module-level singletons in operations_routes
    with patch("audisor_assistant.api.operations_routes._controller", controller), \
         patch("audisor_assistant.api.operations_routes._event_store", event_store):
        yield TestClient(app)


@pytest.fixture()
def suspended_client(tmp_path: Path):
    """Client with a PlanningAdapter that suspends on first call."""
    from operation_controller.controller import FileOperationStore, OperationController
    from operation_controller.event_store import EventStore
    from operation_controller.tool_loop import ProviderResponse

    store_root = tmp_path / "ops"
    store = FileOperationStore(store_root)
    event_store = EventStore(store_root)

    # Provider that requests file_read (causes suspension)
    class SuspendingProvider:
        def __init__(self):
            self._calls = 0

        def call(self, messages, tools, model, max_tokens, timeout_seconds):
            self._calls += 1
            if self._calls == 1:
                return ProviderResponse(
                    tool_calls=[{
                        "id": "call-001",
                        "type": "function",
                        "function": {"name": "file_read", "arguments": '{"path": "main.py"}'},
                    }],
                    usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                )
            return ProviderResponse(
                text='```json\n{"summary": "plan done", "steps": [], "risks": []}\n```',
                finish_reason="stop",
            )

    from operation_controller.adapters.planning import LLMPlanningAdapter
    planning_adapter = LLMPlanningAdapter(SuspendingProvider())

    controller = OperationController(
        store=store,
        event_store=event_store,
        planning_adapter=planning_adapter,
    )

    app = create_app(AssistantService(DeterministicFakeProvider()))
    with patch("audisor_assistant.api.operations_routes._controller", controller), \
         patch("audisor_assistant.api.operations_routes._event_store", event_store):
        yield TestClient(app)


# ─── Basic endpoint tests ────────────────────────────────────────────────────


class TestCreateOperation:
    def test_create_operation_returns_id_and_state(self, client: TestClient) -> None:
        response = client.post(
            "/v1/operations",
            json={"prompt": "fix the auth bug", "source_kind": "task"},
            headers=DEV_HEADERS,
        )
        assert response.status_code == 200
        body = response.json()
        assert "operation_id" in body
        assert body["operation_id"].startswith("op-")
        assert "state" in body

    def test_create_operation_empty_prompt_rejected(self, client: TestClient) -> None:
        response = client.post(
            "/v1/operations",
            json={"prompt": "", "source_kind": "task"},
            headers=DEV_HEADERS,
        )
        assert response.status_code == 422  # Pydantic validation error


class TestGetEvents:
    def test_get_events_after_creation(self, client: TestClient) -> None:
        # Create an operation first
        create_resp = client.post(
            "/v1/operations",
            json={"prompt": "test task", "source_kind": "task"},
            headers=DEV_HEADERS,
        )
        op_id = create_resp.json()["operation_id"]

        # Poll events
        response = client.get(f"/v1/operations/{op_id}/events?after=0", headers=DEV_HEADERS)
        assert response.status_code == 200
        body = response.json()
        assert body["operation_id"] == op_id
        assert isinstance(body["events"], list)
        assert "cursor" in body
        assert "terminal" in body
        # Should have at least the creation and state_transition events
        assert len(body["events"]) >= 1

    def test_get_events_404_for_unknown_operation(self, client: TestClient) -> None:
        response = client.get("/v1/operations/op-nonexistent/events?after=0", headers=DEV_HEADERS)
        assert response.status_code == 404


class TestCancelOperation:
    def test_cancel_operation(self, client: TestClient) -> None:
        create_resp = client.post(
            "/v1/operations",
            json={"prompt": "cancel me", "source_kind": "task"},
            headers=DEV_HEADERS,
        )
        op_id = create_resp.json()["operation_id"]

        response = client.post(f"/v1/operations/{op_id}/cancel", headers=DEV_HEADERS)
        assert response.status_code == 200
        body = response.json()
        assert body["state"] == "cancelled_by_user"


class TestGetStatus:
    def test_get_status(self, client: TestClient) -> None:
        create_resp = client.post(
            "/v1/operations",
            json={"prompt": "status check", "source_kind": "task"},
            headers=DEV_HEADERS,
        )
        op_id = create_resp.json()["operation_id"]

        response = client.get(f"/v1/operations/{op_id}/status", headers=DEV_HEADERS)
        assert response.status_code == 200
        body = response.json()
        assert body["operation_id"] == op_id
        assert "state" in body
        assert "detail" in body

    def test_get_status_404_for_unknown(self, client: TestClient) -> None:
        response = client.get("/v1/operations/op-ghost/status", headers=DEV_HEADERS)
        assert response.status_code == 404


# ─── Resume and replay protection tests ──────────────────────────────────────


class TestResumeOperation:
    def test_resume_on_non_suspended_returns_error(self, client: TestClient) -> None:
        """Resume on a PLANNING operation (not suspended) should error."""
        create_resp = client.post(
            "/v1/operations",
            json={"prompt": "plan something", "source_kind": "task"},
            headers=DEV_HEADERS,
        )
        op_id = create_resp.json()["operation_id"]

        response = client.post(
            f"/v1/operations/{op_id}/resume",
            json={
                "resume_type": "tool_result",
                "suspension_id": "susp-fake",
                "operation_version": 0,
                "payload": {"call_id": "c1", "tool_name": "file_read", "output": "x", "status": "success"},
            },
            headers=DEV_HEADERS,
        )
        # Should be 409 (not suspended) or 400
        assert response.status_code in (400, 409)


# ─── Approval lifecycle API tests ────────────────────────────────────────────


@pytest.fixture()
def approval_client(tmp_path: Path):
    """Client with execution adapter that suspends for approval (shell_exec)."""
    from operation_controller.controller import FileOperationStore, OperationController
    from operation_controller.event_store import EventStore
    from operation_controller.tool_loop import ProviderResponse

    store_root = tmp_path / "ops"
    store = FileOperationStore(store_root)
    event_store = EventStore(store_root)

    # Planning provider that completes immediately with a plan
    class QuickPlanProvider:
        def __init__(self):
            self._calls = 0

        def call(self, messages, tools, model, max_tokens, timeout_seconds):
            self._calls += 1
            return ProviderResponse(
                text='```json\n{"summary": "test plan", "steps": [], "risks": []}\n```',
                finish_reason="stop",
                usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            )

    # Execution provider that requests shell_exec (approval required)
    class ApprovalProvider:
        def __init__(self):
            self._calls = 0

        def call(self, messages, tools, model, max_tokens, timeout_seconds):
            self._calls += 1
            if self._calls == 1:
                return ProviderResponse(
                    tool_calls=[{
                        "id": "exec-c1",
                        "type": "function",
                        "function": {"name": "shell_exec", "arguments": '{"command": "npm test"}'},
                    }],
                    usage={"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
                )
            return ProviderResponse(
                text="done", finish_reason="stop",
                usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            )

    from operation_controller.adapters.planning import LLMPlanningAdapter
    from operation_controller.adapters.execution import LLMExecutionAdapter
    from operation_controller.adapters.review import StubReviewAdapter

    controller = OperationController(
        store=store,
        event_store=event_store,
        planning_adapter=LLMPlanningAdapter(QuickPlanProvider()),
        review_adapter=StubReviewAdapter(),
        execution_adapter=LLMExecutionAdapter(ApprovalProvider()),
    )

    app = create_app(AssistantService(DeterministicFakeProvider()))
    with patch("audisor_assistant.api.operations_routes._controller", controller), \
         patch("audisor_assistant.api.operations_routes._event_store", event_store):
        yield TestClient(app)


class TestApprovalAPI:
    """API-level approval lifecycle tests."""

    def test_approval_resume_with_correct_digest(self, approval_client: TestClient) -> None:
        """End-to-end approval via HTTP: create → approval suspension → grant."""
        # Create operation — should advance to approval suspension
        create_resp = approval_client.post(
            "/v1/operations",
            json={"prompt": "run tests", "source_kind": "task"},
            headers=DEV_HEADERS,
        )
        assert create_resp.status_code == 200
        op_id = create_resp.json()["operation_id"]
        state = create_resp.json()["state"]
        assert state == "suspended_for_approval"

        # Get events to find the suspension_id
        events_resp = approval_client.get(
            f"/v1/operations/{op_id}/events?after=0", headers=DEV_HEADERS,
        )
        events = events_resp.json()["events"]
        approval_events = [e for e in events if e["event_type"] == "tool_approval_requested"]
        assert len(approval_events) == 1
        suspension_id = approval_events[0]["payload"]["suspension_id"]

        # Resume with approval decision
        resume_resp = approval_client.post(
            f"/v1/operations/{op_id}/resume",
            json={
                "resume_type": "approval_decision",
                "suspension_id": suspension_id,
                "operation_version": 0,
                "payload": {"approved": True, "call_id": "exec-c1"},
            },
            headers=DEV_HEADERS,
        )
        assert resume_resp.status_code == 200
        body = resume_resp.json()
        assert body["state"] == "suspended_for_tool_result"
        assert body["detail"]["approved"] is True

    def test_approval_resume_with_wrong_suspension_id(self, approval_client: TestClient) -> None:
        """Approval with wrong suspension_id returns 409."""
        create_resp = approval_client.post(
            "/v1/operations",
            json={"prompt": "run tests", "source_kind": "task"},
            headers=DEV_HEADERS,
        )
        op_id = create_resp.json()["operation_id"]

        resume_resp = approval_client.post(
            f"/v1/operations/{op_id}/resume",
            json={
                "resume_type": "approval_decision",
                "suspension_id": "susp-wrong-id",
                "operation_version": 0,
                "payload": {"approved": True, "call_id": "exec-c1"},
            },
            headers=DEV_HEADERS,
        )
        assert resume_resp.status_code == 409

    def test_approval_resume_after_cancel(self, approval_client: TestClient) -> None:
        """Approval after cancel returns 409."""
        create_resp = approval_client.post(
            "/v1/operations",
            json={"prompt": "run tests", "source_kind": "task"},
            headers=DEV_HEADERS,
        )
        op_id = create_resp.json()["operation_id"]

        # Cancel first
        cancel_resp = approval_client.post(
            f"/v1/operations/{op_id}/cancel", headers=DEV_HEADERS,
        )
        assert cancel_resp.status_code == 200

        # Try to approve
        resume_resp = approval_client.post(
            f"/v1/operations/{op_id}/resume",
            json={
                "resume_type": "approval_decision",
                "suspension_id": "susp-any",
                "operation_version": 0,
                "payload": {"approved": True, "call_id": "exec-c1"},
            },
            headers=DEV_HEADERS,
        )
        assert resume_resp.status_code in (400, 409)

    def test_resume_with_valid_tool_result(self, suspended_client: TestClient) -> None:
        """Create op that suspends, then resume with tool result."""
        # Create operation — PlanningAdapter will suspend for file_read
        create_resp = suspended_client.post(
            "/v1/operations",
            json={"prompt": "read and plan", "source_kind": "task"},
            headers=DEV_HEADERS,
        )
        assert create_resp.status_code == 200
        body = create_resp.json()
        op_id = body["operation_id"]
        # State should be suspended
        assert body["state"] == "suspended_for_tool_result"

        # Get status to confirm suspension
        status_resp = suspended_client.get(f"/v1/operations/{op_id}/status", headers=DEV_HEADERS)
        assert status_resp.json()["state"] == "suspended_for_tool_result"

        # Resume with tool result
        resume_resp = suspended_client.post(
            f"/v1/operations/{op_id}/resume",
            json={
                "resume_type": "tool_result",
                "suspension_id": "susp-any",
                "operation_version": 0,
                "payload": {
                    "call_id": "call-001",
                    "tool_name": "file_read",
                    "output": "file contents here",
                    "error": None,
                    "status": "success",
                },
            },
            headers=DEV_HEADERS,
        )
        assert resume_resp.status_code == 200
        resume_body = resume_resp.json()
        # After resume, adapter should complete planning
        # State should have advanced past suspension
        assert resume_body["state"] != "suspended_for_tool_result"

    def test_resume_on_already_terminal_returns_error(self, client: TestClient) -> None:
        """Cancel then try resume — should fail."""
        create_resp = client.post(
            "/v1/operations",
            json={"prompt": "terminate me", "source_kind": "task"},
            headers=DEV_HEADERS,
        )
        op_id = create_resp.json()["operation_id"]

        # Cancel it
        client.post(f"/v1/operations/{op_id}/cancel", headers=DEV_HEADERS)

        # Try to resume
        response = client.post(
            f"/v1/operations/{op_id}/resume",
            json={
                "resume_type": "tool_result",
                "suspension_id": "susp-x",
                "operation_version": 0,
                "payload": {"call_id": "c1", "tool_name": "x", "output": "y", "status": "success"},
            },
            headers=DEV_HEADERS,
        )
        assert response.status_code in (400, 409)
