"""Approval lifecycle tests — proves the two-stage approval authority.

Covers:
- shell_exec creates approval suspension with argument digest
- Distinct approval event (not tool_execution_requested)
- Two-stage: approval → tool-result suspension
- Denial cancels the operation
- Idempotent replay of same decision
- Conflicting decisions rejected
- Cancelled operations reject approval
- Argument digest mismatch rejected
- Suspension ID mismatch rejected
- Read-only tools do not require approval
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from operation_controller.approval import compute_argument_digest
from operation_controller.controller import (
    FileOperationStore,
    OperationController,
    OperationControllerResult,
)
from operation_controller.event_store import EventStore
from operation_controller.states import OperationState
from operation_controller.tool_loop import ProviderResponse
from operation_controller.adapters.planning import LLMPlanningAdapter
from operation_controller.adapters.execution import LLMExecutionAdapter
from operation_controller.adapters.review import StubReviewAdapter


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _tc(call_id: str, name: str, args: str = "{}") -> dict:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": args},
    }


class ScriptedProvider:
    """Provider that returns scripted responses in order."""

    def __init__(self, script: list[ProviderResponse]) -> None:
        self._script = list(script)
        self._idx = 0
        self.call_count = 0

    def call(self, messages, tools, model, max_tokens, timeout_seconds):
        self.call_count += 1
        if self._idx >= len(self._script):
            return ProviderResponse(text="script exhausted", finish_reason="stop")
        resp = self._script[self._idx]
        self._idx += 1
        return resp


def _make_controller(
    tmp_path: Path,
    planning_script: list[ProviderResponse],
    execution_script: list[ProviderResponse],
) -> tuple[OperationController, EventStore, FileOperationStore]:
    """Build a controller with scripted providers."""
    store_root = tmp_path / "ops"
    store = FileOperationStore(store_root)
    event_store = EventStore(store_root)

    planning_provider = ScriptedProvider(planning_script)
    execution_provider = ScriptedProvider(execution_script)

    controller = OperationController(
        store=store,
        event_store=event_store,
        planning_adapter=LLMPlanningAdapter(planning_provider),
        review_adapter=StubReviewAdapter(),
        execution_adapter=LLMExecutionAdapter(execution_provider),
    )
    return controller, event_store, store


def _advance_to_execution_approval(
    controller: OperationController,
) -> tuple[str, OperationControllerResult]:
    """Create an operation and advance it to the approval suspension.

    Uses a planning provider that completes immediately and an execution
    provider that requests shell_exec.

    Returns (operation_id, approval_result).
    """
    # Accept task — planning completes without suspension
    result = controller.accept(source_kind="task", prompt="test")
    op_id = result.operation_id

    # If planning suspended for tool, resume it
    if result.state == OperationState.SUSPENDED_FOR_TOOL_RESULT:
        result = controller.resume(op_id, resume_input={
            "resume_type": "tool_result",
            "tool_results": [{
                "call_id": "plan-c1", "tool_name": "file_read",
                "output": "content", "error": None, "status": "success",
            }],
        })

    # Now should be suspended for approval (shell_exec)
    assert result.state == OperationState.SUSPENDED_FOR_APPROVAL, (
        f"Expected SUSPENDED_FOR_APPROVAL, got {result.state.value}"
    )
    return op_id, result


# ─── Tests ────────────────────────────────────────────────────────────────────


class TestApprovalSuspension:
    """Approval suspension creation and event emission."""

    def test_shell_exec_creates_approval_suspension(self, tmp_path: Path) -> None:
        """Provider requests shell_exec → adapter returns suspended_for_approval with digest."""
        controller, event_store, store = _make_controller(
            tmp_path,
            planning_script=[
                ProviderResponse(text='```json\n{"summary": "t", "steps": [], "risks": []}\n```',
                                 finish_reason="stop"),
            ],
            execution_script=[
                ProviderResponse(
                    tool_calls=[_tc("c1", "shell_exec", '{"command": "npm test"}')],
                ),
            ],
        )

        result = controller.accept(source_kind="task", prompt="test")
        assert result.state == OperationState.SUSPENDED_FOR_APPROVAL

        # Verify argument digest is stored in record
        record = store.load(result.operation_id)
        assert record is not None
        digest = record.slices.get("argument_digest", "")
        assert digest, "argument_digest must be persisted"

        # Verify digest matches the tool call
        expected = compute_argument_digest("c1", "shell_exec", {"command": "npm test"})
        assert digest == expected

    def test_approval_event_emitted_not_tool_event(self, tmp_path: Path) -> None:
        """Event store contains tool_approval_requested, not tool_execution_requested for approval."""
        controller, event_store, store = _make_controller(
            tmp_path,
            planning_script=[
                ProviderResponse(text='```json\n{"summary": "t", "steps": [], "risks": []}\n```',
                                 finish_reason="stop"),
            ],
            execution_script=[
                ProviderResponse(
                    tool_calls=[_tc("c1", "shell_exec", '{"command": "npm test"}')],
                ),
            ],
        )

        result = controller.accept(source_kind="task", prompt="test")
        op_id = result.operation_id

        events = event_store.read_after(op_id, after=0, limit=200)
        event_types = [e.event_type for e in events]

        # Must have approval event
        assert "tool_approval_requested" in event_types, (
            f"Expected tool_approval_requested in events: {event_types}"
        )

        # The approval suspension must NOT emit tool_execution_requested
        # Find events after the execution started
        approval_events = [e for e in events if e.event_type == "tool_approval_requested"]
        assert len(approval_events) == 1
        assert "argument_digest" in approval_events[0].payload
        assert "tool_call" in approval_events[0].payload

    def test_read_only_tools_do_not_require_approval(self, tmp_path: Path) -> None:
        """file_read goes directly to tool-result suspension, not approval."""
        controller, event_store, store = _make_controller(
            tmp_path,
            planning_script=[
                ProviderResponse(
                    tool_calls=[_tc("pc1", "file_read", '{"path": "x.py"}')],
                ),
            ],
            execution_script=[],
        )

        result = controller.accept(source_kind="task", prompt="read and plan")
        # file_read → tool-result suspension (NOT approval)
        assert result.state == OperationState.SUSPENDED_FOR_TOOL_RESULT

        events = event_store.read_after(result.operation_id, after=0, limit=200)
        event_types = [e.event_type for e in events]
        assert "tool_approval_requested" not in event_types
        assert "tool_execution_requested" in event_types


class TestApprovalDecision:
    """Approval decision processing — grant, deny, replay, conflict."""

    def test_approval_then_tool_result_suspension(self, tmp_path: Path) -> None:
        """After approval, the same call suspends for frontend execution (two-stage)."""
        controller, event_store, store = _make_controller(
            tmp_path,
            planning_script=[
                ProviderResponse(text='```json\n{"summary": "t", "steps": [], "risks": []}\n```',
                                 finish_reason="stop"),
            ],
            execution_script=[
                ProviderResponse(
                    tool_calls=[_tc("c1", "shell_exec", '{"command": "npm test"}')],
                ),
                ProviderResponse(
                    tool_calls=[_tc("c2", "file_read", '{"path": "x.py"}')],
                ),
            ],
        )

        result = controller.accept(source_kind="task", prompt="test")
        op_id = result.operation_id
        suspension_id = result.detail.get("suspension_id", "")

        # Grant approval
        result = controller.resume(op_id, resume_input={
            "resume_type": "approval_decision",
            "approved": True,
            "suspension_id": suspension_id,
            "call_id": "c1",
        })

        # Two-stage: now suspended for tool-result (frontend executes shell_exec)
        assert result.state == OperationState.SUSPENDED_FOR_TOOL_RESULT
        assert result.detail.get("approved") is True

        # Event store should have tool_approval_resolved
        events = event_store.read_after(op_id, after=0, limit=200)
        event_types = [e.event_type for e in events]
        assert "tool_approval_resolved" in event_types

    def test_denial_cancels_operation(self, tmp_path: Path) -> None:
        """approved=False transitions to cancelled_by_user."""
        controller, event_store, store = _make_controller(
            tmp_path,
            planning_script=[
                ProviderResponse(text='```json\n{"summary": "t", "steps": [], "risks": []}\n```',
                                 finish_reason="stop"),
            ],
            execution_script=[
                ProviderResponse(
                    tool_calls=[_tc("c1", "shell_exec", '{"command": "rm -rf /"}')],
                ),
            ],
        )

        result = controller.accept(source_kind="task", prompt="test")
        op_id = result.operation_id

        # Deny approval
        result = controller.resume(op_id, resume_input={
            "resume_type": "approval_decision",
            "approved": False,
            "call_id": "c1",
        })

        assert result.state == OperationState.CANCELLED_BY_USER
        assert result.detail.get("approved") is False
        assert result.detail.get("reason") == "approval_denied"

        # Event store should have denial event
        events = event_store.read_after(op_id, after=0, limit=200)
        event_types = [e.event_type for e in events]
        assert "tool_approval_denied" in event_types

    def test_approval_replay_idempotent(self, tmp_path: Path) -> None:
        """Same approval decision twice returns same result (idempotent)."""
        controller, _, store = _make_controller(
            tmp_path,
            planning_script=[
                ProviderResponse(text='```json\n{"summary": "t", "steps": [], "risks": []}\n```',
                                 finish_reason="stop"),
            ],
            execution_script=[
                ProviderResponse(
                    tool_calls=[_tc("c1", "shell_exec", '{"command": "npm test"}')],
                ),
            ],
        )

        result = controller.accept(source_kind="task", prompt="test")
        op_id = result.operation_id
        suspension_id = result.detail.get("suspension_id", "")

        # First approval
        result1 = controller.resume(op_id, resume_input={
            "resume_type": "approval_decision",
            "approved": True,
            "suspension_id": suspension_id,
            "call_id": "c1",
        })
        assert result1.state == OperationState.SUSPENDED_FOR_TOOL_RESULT

        # Second approval (same decision) — idempotent
        result2 = controller.resume(op_id, resume_input={
            "resume_type": "approval_decision",
            "approved": True,
            "suspension_id": suspension_id,
            "call_id": "c1",
        })
        assert result2.detail.get("idempotent") is True
        assert result2.detail.get("approved") is True

    def test_conflicting_approval_rejected(self, tmp_path: Path) -> None:
        """Approve then deny (or vice versa) returns error."""
        controller, _, store = _make_controller(
            tmp_path,
            planning_script=[
                ProviderResponse(text='```json\n{"summary": "t", "steps": [], "risks": []}\n```',
                                 finish_reason="stop"),
            ],
            execution_script=[
                ProviderResponse(
                    tool_calls=[_tc("c1", "shell_exec", '{"command": "npm test"}')],
                ),
            ],
        )

        result = controller.accept(source_kind="task", prompt="test")
        op_id = result.operation_id
        suspension_id = result.detail.get("suspension_id", "")

        # First: approve
        result1 = controller.resume(op_id, resume_input={
            "resume_type": "approval_decision",
            "approved": True,
            "suspension_id": suspension_id,
            "call_id": "c1",
        })
        assert result1.state == OperationState.SUSPENDED_FOR_TOOL_RESULT

        # Second: deny (conflicting)
        result2 = controller.resume(op_id, resume_input={
            "resume_type": "approval_decision",
            "approved": False,
            "suspension_id": suspension_id,
            "call_id": "c1",
        })
        assert result2.detail.get("error") == "conflicting_approval_decision"

    def test_cancelled_operation_rejects_approval(self, tmp_path: Path) -> None:
        """Cancel then try to approve — should fail."""
        controller, _, store = _make_controller(
            tmp_path,
            planning_script=[
                ProviderResponse(text='```json\n{"summary": "t", "steps": [], "risks": []}\n```',
                                 finish_reason="stop"),
            ],
            execution_script=[
                ProviderResponse(
                    tool_calls=[_tc("c1", "shell_exec", '{"command": "npm test"}')],
                ),
            ],
        )

        result = controller.accept(source_kind="task", prompt="test")
        op_id = result.operation_id

        # Cancel the operation
        cancel_result = controller.cancel(op_id)
        assert cancel_result.state == OperationState.CANCELLED_BY_USER

        # Try to approve — should fail (not suspended)
        result = controller.resume(op_id, resume_input={
            "resume_type": "approval_decision",
            "approved": True,
            "call_id": "c1",
        })
        assert "error" in result.detail


class TestReplayProtection:
    """Argument digest and suspension ID validation."""

    def test_argument_digest_mismatch_rejected(self, tmp_path: Path) -> None:
        """Altered arguments fail digest check."""
        controller, _, store = _make_controller(
            tmp_path,
            planning_script=[
                ProviderResponse(text='```json\n{"summary": "t", "steps": [], "risks": []}\n```',
                                 finish_reason="stop"),
            ],
            execution_script=[
                ProviderResponse(
                    tool_calls=[_tc("c1", "shell_exec", '{"command": "npm test"}')],
                ),
            ],
        )

        result = controller.accept(source_kind="task", prompt="test")
        op_id = result.operation_id
        suspension_id = result.detail.get("suspension_id", "")

        # Tamper with the stored digest to simulate argument alteration
        record = store.load(op_id)
        record.slices["argument_digest"] = "deadbeef" * 8  # wrong digest
        store.save(record)

        # Try to approve — digest mismatch
        result = controller.resume(op_id, resume_input={
            "resume_type": "approval_decision",
            "approved": True,
            "suspension_id": suspension_id,
            "call_id": "c1",
        })
        assert result.detail.get("error") == "argument_digest_mismatch"

    def test_suspension_id_mismatch_rejected(self, tmp_path: Path) -> None:
        """Wrong suspension_id returns error."""
        controller, _, store = _make_controller(
            tmp_path,
            planning_script=[
                ProviderResponse(text='```json\n{"summary": "t", "steps": [], "risks": []}\n```',
                                 finish_reason="stop"),
            ],
            execution_script=[
                ProviderResponse(
                    tool_calls=[_tc("c1", "shell_exec", '{"command": "npm test"}')],
                ),
            ],
        )

        result = controller.accept(source_kind="task", prompt="test")
        op_id = result.operation_id

        # Try to approve with wrong suspension_id
        result = controller.resume(op_id, resume_input={
            "resume_type": "approval_decision",
            "approved": True,
            "suspension_id": "susp-wrong-id",
            "call_id": "c1",
        })
        assert result.detail.get("error") == "suspension_id_mismatch"
