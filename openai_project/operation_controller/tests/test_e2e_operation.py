"""End-to-end operation lifecycle test.

Proves the FULL flow through the controller with real adapters and
a scripted fake provider:

1. Accept task -> PlanningAdapter suspends for file_read
2. Resume with tool result -> plan generated
3. Review (stub) passes -> advance to execution
4. ExecutionAdapter suspends for approval (shell_exec)
5. Resume with approval -> continues
6. ExecutionAdapter suspends for frontend tool (file_read)
7. Resume with tool result -> execution completes
8. Terminal state COMPLETED
9. Event store contains full lifecycle (persisted on disk)
10. Fresh EventStore from same path reconstructs all events
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

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


# ─── Scripted provider ────────────────────────────────────────────────────────


def _tc(call_id: str, name: str, args: str = "{}") -> dict:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": args},
    }


class ScriptedProvider:
    """Provider that returns different responses based on call count.

    Tracks all calls for assertion.
    """

    def __init__(self, script: list[ProviderResponse]) -> None:
        self._script = list(script)
        self._idx = 0
        self.call_count = 0

    def call(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        model: str | None,
        max_tokens: int,
        timeout_seconds: float,
    ) -> ProviderResponse:
        self.call_count += 1
        if self._idx >= len(self._script):
            return ProviderResponse(text="script exhausted", finish_reason="stop")
        resp = self._script[self._idx]
        self._idx += 1
        return resp


# ─── The test ─────────────────────────────────────────────────────────────────


def test_full_operation_lifecycle(tmp_path: Path) -> None:
    """Complete lifecycle: task -> plan -> review -> execute -> complete."""
    store_root = tmp_path / "ops"
    store = FileOperationStore(store_root)
    event_store = EventStore(store_root)

    # Planning provider script:
    # Call 1: requests file_read (suspends)
    # Call 2 (after resume): returns plan JSON
    planning_provider = ScriptedProvider([
        ProviderResponse(
            tool_calls=[_tc("plan-c1", "file_read", '{"path": "src/auth.py"}')],
            usage={"prompt_tokens": 50, "completion_tokens": 20, "total_tokens": 70},
        ),
        ProviderResponse(
            text='```json\n{"summary": "Fix auth module", "steps": [{"description": "patch login", "files": ["auth.py"]}], "risks": []}\n```',
            finish_reason="stop",
            usage={"prompt_tokens": 100, "completion_tokens": 60, "total_tokens": 160},
        ),
    ])

    # Execution provider script:
    # Call 1: requests shell_exec (approval required -> suspends)
    # Call 2 (after approval): requests file_read (frontend tool -> suspends)
    # Call 3 (after tool result): completes
    execution_provider = ScriptedProvider([
        ProviderResponse(
            tool_calls=[_tc("exec-c1", "shell_exec", '{"command": "npm test"}')],
            usage={"prompt_tokens": 80, "completion_tokens": 30, "total_tokens": 110},
        ),
        ProviderResponse(
            tool_calls=[_tc("exec-c2", "file_read", '{"path": "auth.py"}')],
            usage={"prompt_tokens": 120, "completion_tokens": 40, "total_tokens": 160},
        ),
        ProviderResponse(
            text="All steps completed. Auth module patched successfully.",
            finish_reason="stop",
            usage={"prompt_tokens": 150, "completion_tokens": 50, "total_tokens": 200},
        ),
    ])

    # Build adapters
    planning_adapter = LLMPlanningAdapter(planning_provider)
    review_adapter = StubReviewAdapter()
    execution_adapter = LLMExecutionAdapter(execution_provider)

    # Build controller
    controller = OperationController(
        store=store,
        event_store=event_store,
        planning_adapter=planning_adapter,
        review_adapter=review_adapter,
        execution_adapter=execution_adapter,
    )

    # ── Step 1: Accept task ──
    result = controller.accept(source_kind="task", prompt="fix the auth bug")
    op_id = result.operation_id

    # PlanningAdapter should have suspended for file_read
    assert result.state == OperationState.SUSPENDED_FOR_TOOL_RESULT, (
        f"Expected SUSPENDED_FOR_TOOL_RESULT, got {result.state.value}"
    )

    # ── Step 2: Verify event store has initial events ──
    events = event_store.read_after(op_id, after=0, limit=100)
    event_types = [e.event_type for e in events]
    assert "operation_created" in event_types
    assert "state_transition" in event_types
    assert "tool_execution_requested" in event_types

    # ── Step 3: Resume with file_read result ──
    result = controller.resume(op_id, resume_input={
        "resume_type": "tool_result",
        "tool_results": [{
            "call_id": "plan-c1",
            "tool_name": "file_read",
            "output": "def login(user, password):\n    return authenticate(user, password)",
            "error": None,
            "status": "success",
        }],
    })

    # After resume: planning completes, review passes, execution starts and
    # immediately suspends for approval (shell_exec)
    assert result.state == OperationState.SUSPENDED_FOR_APPROVAL, (
        f"Expected SUSPENDED_FOR_APPROVAL, got {result.state.value}"
    )

    # ── Step 4: Resume with approval (approved=True) ──
    # Extract suspension_id for replay protection
    approval_suspension_id = result.detail.get("suspension_id", "")

    result = controller.resume(op_id, resume_input={
        "resume_type": "approval_decision",
        "approved": True,
        "suspension_id": approval_suspension_id,
        "call_id": "exec-c1",
    })

    # After approval: two-stage — transitions to SUSPENDED_FOR_TOOL_RESULT
    # for frontend execution of the approved shell_exec call
    assert result.state == OperationState.SUSPENDED_FOR_TOOL_RESULT, (
        f"Expected SUSPENDED_FOR_TOOL_RESULT (two-stage), got {result.state.value}"
    )
    assert result.detail.get("approved") is True

    # ── Step 5: Resume after approval — provider continues, requests file_read ──
    result = controller.resume(op_id, resume_input={
        "resume_type": "tool_result",
        "tool_results": [{
            "call_id": "exec-c2",
            "tool_name": "file_read",
            "output": "def login(user, password):\n    return authenticate_secure(user, password)",
            "error": None,
            "status": "success",
        }],
    })

    # Provider was called and returned file_read (frontend tool) → suspended again
    assert result.state == OperationState.SUSPENDED_FOR_TOOL_RESULT, (
        f"Expected SUSPENDED_FOR_TOOL_RESULT (file_read), got {result.state.value}"
    )

    # ── Step 6: Resume with file_read result — provider completes ──
    result = controller.resume(op_id, resume_input={
        "resume_type": "tool_result",
        "tool_results": [{
            "call_id": "exec-c2",
            "tool_name": "file_read",
            "output": "def login(user, password):\n    return authenticate_secure(user, password)",
            "error": None,
            "status": "success",
        }],
    })

    # After final resume: execution completes, operation reaches terminal state
    assert result.state == OperationState.COMPLETED, (
        f"Expected COMPLETED, got {result.state.value}"
    )

    # ── Step 7: Verify full event store lifecycle ──
    all_events = event_store.read_after(op_id, after=0, limit=200)
    states_in_events = [e.state for e in all_events]
    # Should have traversed: received -> planning -> suspended -> planning ->
    # plan_ready -> reviewing -> sandbox_ready -> sandbox_running -> suspended ->
    # sandbox_running -> suspended -> sandbox_running -> verifying -> ready_to_apply -> completed
    assert "planning" in states_in_events
    assert "suspended_for_tool_result" in states_in_events
    assert "suspended_for_approval" in states_in_events
    assert "completed" in states_in_events

    # ── Step 8: Persistence proof — new EventStore reads same events from disk ──
    fresh_store = EventStore(store_root)
    fresh_events = fresh_store.read_after(op_id, after=0, limit=200)
    assert len(fresh_events) == len(all_events)
    assert [e.sequence for e in fresh_events] == [e.sequence for e in all_events]

    # ── Step 9: Operation record persisted correctly ──
    record = store.load(op_id)
    assert record is not None
    assert record.state == OperationState.COMPLETED
    # History should show the full traversal
    assert len(record.history) >= 6  # Multiple transitions recorded

    # ── Step 10: Usage tracked across provider calls ──
    # Planning made 2 calls, execution made 3 calls = 5 total
    assert planning_provider.call_count == 2
    assert execution_provider.call_count == 3


def test_operation_cancelled_mid_suspension(tmp_path: Path) -> None:
    """Cancel during suspension — proves cancel works on suspended state."""
    store_root = tmp_path / "ops"
    store = FileOperationStore(store_root)
    event_store = EventStore(store_root)

    provider = ScriptedProvider([
        ProviderResponse(
            tool_calls=[_tc("c1", "file_read", '{"path": "x.py"}')],
        ),
    ])
    planning_adapter = LLMPlanningAdapter(provider)
    controller = OperationController(
        store=store,
        event_store=event_store,
        planning_adapter=planning_adapter,
    )

    result = controller.accept(source_kind="task", prompt="read and plan")
    assert result.state == OperationState.SUSPENDED_FOR_TOOL_RESULT

    # Cancel while suspended
    cancel_result = controller.cancel(result.operation_id)
    assert cancel_result.state == OperationState.CANCELLED_BY_USER

    # Trying to resume after cancel should fail
    resume_result = controller.resume(result.operation_id, resume_input={})
    assert "error" in resume_result.detail


def test_duplicate_resume_on_completed_operation(tmp_path: Path) -> None:
    """Resume on a completed operation returns error."""
    store_root = tmp_path / "ops"
    store = FileOperationStore(store_root)
    event_store = EventStore(store_root)

    # Provider that completes immediately (no tools)
    provider = ScriptedProvider([
        ProviderResponse(
            text='```json\n{"summary": "done", "steps": [], "risks": []}\n```',
            finish_reason="stop",
        ),
        ProviderResponse(text="executed", finish_reason="stop"),
    ])
    planning_adapter = LLMPlanningAdapter(provider)
    review_adapter = StubReviewAdapter()
    execution_adapter = LLMExecutionAdapter(ScriptedProvider([
        ProviderResponse(text="all done", finish_reason="stop"),
    ]))

    controller = OperationController(
        store=store,
        event_store=event_store,
        planning_adapter=planning_adapter,
        review_adapter=review_adapter,
        execution_adapter=execution_adapter,
    )

    result = controller.accept(source_kind="task", prompt="simple task")
    assert result.state == OperationState.COMPLETED

    # Resume on completed — should error
    resume_result = controller.resume(result.operation_id, resume_input={})
    assert "error" in resume_result.detail
    assert "not suspended" in str(resume_result.detail.get("error", ""))
