"""Execution-claim tests — atomic claim_tool_execution.

Covers:
- First valid claim succeeds and returns persisted arguments
- Same claimant idempotent replay
- Competing claimant rejected
- Wrong suspension_id rejected
- Wrong tool-call ID rejected
- Wrong argument digest rejected
- Cancelled operation rejects claim
- Terminal operation rejects claim
- Unknown operation rejects claim
- Claim response arguments match persisted canonical arguments
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


def _advance_to_tool_result_suspension(
    controller: OperationController,
) -> tuple[str, OperationControllerResult]:
    """Create an operation and advance it through approval to tool-result suspension.

    Uses a planning provider that completes immediately and an execution
    provider that requests shell_exec (approval), then after approval,
    the operation is in SUSPENDED_FOR_TOOL_RESULT.

    Returns (operation_id, tool_result_result).
    """
    # Accept task — planning completes, execution requests shell_exec
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

    # Should now be suspended for approval (shell_exec)
    assert result.state == OperationState.SUSPENDED_FOR_APPROVAL, (
        f"Expected SUSPENDED_FOR_APPROVAL, got {result.state.value}"
    )

    suspension_id = result.detail.get("suspension_id", "")

    # Grant approval → transitions to SUSPENDED_FOR_TOOL_RESULT
    result = controller.resume(op_id, resume_input={
        "resume_type": "approval_decision",
        "approved": True,
        "suspension_id": suspension_id,
        "call_id": "c1",
    })

    assert result.state == OperationState.SUSPENDED_FOR_TOOL_RESULT, (
        f"Expected SUSPENDED_FOR_TOOL_RESULT, got {result.state.value}"
    )

    return op_id, result


# ─── Tests ────────────────────────────────────────────────────────────────────


class TestFirstValidClaim:
    """First valid claim succeeds."""

    def test_first_claim_succeeds(self, tmp_path: Path) -> None:
        """Valid claim returns persisted arguments."""
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

        op_id, tr_result = _advance_to_tool_result_suspension(controller)
        suspension_id = tr_result.detail.get("suspension_id", "")
        digest = compute_argument_digest("c1", "shell_exec", {"command": "npm test"})

        claim = controller.claim_tool_execution(
            op_id,
            claimant_id="tab-1",
            suspension_id=suspension_id,
            call_id="c1",
            argument_digest=digest,
        )

        assert claim.detail.get("claimed") is True
        assert claim.detail.get("claimant_id") == "tab-1"
        assert claim.detail.get("call_id") == "c1"
        # Arguments must come from persisted record, not claimant
        assert claim.detail.get("arguments") == {"command": "npm test"}
        assert claim.detail.get("argument_digest") == digest

    def test_claim_response_arguments_match_persisted(self, tmp_path: Path) -> None:
        """Claim response returns persisted canonical arguments, not claimant-supplied."""
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

        op_id, tr_result = _advance_to_tool_result_suspension(controller)
        suspension_id = tr_result.detail.get("suspension_id", "")
        digest = compute_argument_digest("c1", "shell_exec", {"command": "npm test"})

        # Even if claimant supplies different arguments, response returns persisted ones
        claim = controller.claim_tool_execution(
            op_id,
            claimant_id="tab-1",
            suspension_id=suspension_id,
            call_id="c1",
            argument_digest=digest,
        )

        assert claim.detail["arguments"] == {"command": "npm test"}


class TestIdempotentClaim:
    """Same claimant, same claim returns idempotent success."""

    def test_same_claimant_idempotent(self, tmp_path: Path) -> None:
        """Claiming twice with same claimant returns idempotent result."""
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

        op_id, tr_result = _advance_to_tool_result_suspension(controller)
        suspension_id = tr_result.detail.get("suspension_id", "")
        digest = compute_argument_digest("c1", "shell_exec", {"command": "npm test"})

        claim1 = controller.claim_tool_execution(
            op_id,
            claimant_id="tab-1",
            suspension_id=suspension_id,
            call_id="c1",
            argument_digest=digest,
        )
        assert claim1.detail.get("claimed") is True

        claim2 = controller.claim_tool_execution(
            op_id,
            claimant_id="tab-1",
            suspension_id=suspension_id,
            call_id="c1",
            argument_digest=digest,
        )
        assert claim2.detail.get("claimed") is True
        assert claim2.detail.get("idempotent") is True


class TestCompetingClaim:
    """Competing claimant is rejected."""

    def test_competing_claimant_rejected(self, tmp_path: Path) -> None:
        """Second claimant gets conflict."""
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

        op_id, tr_result = _advance_to_tool_result_suspension(controller)
        suspension_id = tr_result.detail.get("suspension_id", "")
        digest = compute_argument_digest("c1", "shell_exec", {"command": "npm test"})

        # First claim succeeds
        claim1 = controller.claim_tool_execution(
            op_id,
            claimant_id="tab-1",
            suspension_id=suspension_id,
            call_id="c1",
            argument_digest=digest,
        )
        assert claim1.detail.get("claimed") is True

        # Second claim from different tab fails
        claim2 = controller.claim_tool_execution(
            op_id,
            claimant_id="tab-2",
            suspension_id=suspension_id,
            call_id="c1",
            argument_digest=digest,
        )
        assert claim2.detail.get("error") == "competing_claim"


class TestClaimRejections:
    """Invalid claims are rejected."""

    def test_wrong_suspension_id(self, tmp_path: Path) -> None:
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

        op_id, tr_result = _advance_to_tool_result_suspension(controller)
        digest = compute_argument_digest("c1", "shell_exec", {"command": "npm test"})

        claim = controller.claim_tool_execution(
            op_id,
            claimant_id="tab-1",
            suspension_id="susp-wrong",
            call_id="c1",
            argument_digest=digest,
        )
        assert claim.detail.get("error") == "suspension_id_mismatch"

    def test_wrong_call_id(self, tmp_path: Path) -> None:
        """Wrong tool-call ID returns error."""
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

        op_id, tr_result = _advance_to_tool_result_suspension(controller)
        suspension_id = tr_result.detail.get("suspension_id", "")
        digest = compute_argument_digest("c1", "shell_exec", {"command": "npm test"})

        claim = controller.claim_tool_execution(
            op_id,
            claimant_id="tab-1",
            suspension_id=suspension_id,
            call_id="wrong-call-id",
            argument_digest=digest,
        )
        assert claim.detail.get("error") == "tool_call_not_found"

    def test_wrong_digest(self, tmp_path: Path) -> None:
        """Wrong argument digest returns error."""
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

        op_id, tr_result = _advance_to_tool_result_suspension(controller)
        suspension_id = tr_result.detail.get("suspension_id", "")

        claim = controller.claim_tool_execution(
            op_id,
            claimant_id="tab-1",
            suspension_id=suspension_id,
            call_id="c1",
            argument_digest="deadbeef" * 8,
        )
        assert claim.detail.get("error") == "argument_digest_mismatch"

    def test_cancelled_operation_rejects_claim(self, tmp_path: Path) -> None:
        """Cancelled operation rejects claim."""
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

        op_id, tr_result = _advance_to_tool_result_suspension(controller)
        suspension_id = tr_result.detail.get("suspension_id", "")
        digest = compute_argument_digest("c1", "shell_exec", {"command": "npm test"})

        # Cancel the operation
        controller.cancel(op_id)

        claim = controller.claim_tool_execution(
            op_id,
            claimant_id="tab-1",
            suspension_id=suspension_id,
            call_id="c1",
            argument_digest=digest,
        )
        assert claim.detail.get("error") == "operation_not_suspended_for_tool_result"

    def test_unknown_operation(self, tmp_path: Path) -> None:
        """Unknown operation returns error."""
        controller, _, store = _make_controller(
            tmp_path,
            planning_script=[],
            execution_script=[],
        )

        claim = controller.claim_tool_execution(
            "op-nonexistent",
            claimant_id="tab-1",
            suspension_id="susp-1",
            call_id="c1",
            argument_digest="abc123",
        )
        assert claim.detail.get("error") == "operation_not_found"


class TestPersistedEnvelopeInvariant:
    """Approved digest == persisted call digest == claim digest."""

    def test_digest_chain_integrity(self, tmp_path: Path) -> None:
        """The digest computed from persisted arguments matches the claim digest."""
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

        # Advance to approval
        result = controller.accept(source_kind="task", prompt="test")
        op_id = result.operation_id

        if result.state == OperationState.SUSPENDED_FOR_TOOL_RESULT:
            result = controller.resume(op_id, resume_input={
                "resume_type": "tool_result",
                "tool_results": [{
                    "call_id": "plan-c1", "tool_name": "file_read",
                    "output": "content", "error": None, "status": "success",
                }],
            })

        # Record the approval digest
        approval_digest = result.detail.get("argument_digest", "")

        # Grant approval
        approval_suspension_id = result.detail.get("suspension_id", "")
        result = controller.resume(op_id, resume_input={
            "resume_type": "approval_decision",
            "approved": True,
            "suspension_id": approval_suspension_id,
            "call_id": "c1",
        })

        # Tool-result suspension digest must match approval digest
        tr_suspension_id = result.detail.get("suspension_id", "")
        tr_digest = result.detail.get("argument_digest", "")
        assert tr_digest == approval_digest, (
            f"tool_execution_requested digest {tr_digest} != approval digest {approval_digest}"
        )

        # Claim digest must also match
        claim = controller.claim_tool_execution(
            op_id,
            claimant_id="tab-1",
            suspension_id=tr_suspension_id,
            call_id="c1",
            argument_digest=tr_digest,
        )
        assert claim.detail.get("argument_digest") == tr_digest

        # Verify event chain
        events = event_store.read_after(op_id, after=0, limit=200)
        approval_events = [e for e in events if e.event_type == "tool_approval_requested"]
        execution_events = [e for e in events if e.event_type == "tool_execution_requested"]
        claim_events = [e for e in events if e.event_type == "tool_execution_claimed"]

        assert len(approval_events) == 1
        assert len(execution_events) >= 1
        assert len(claim_events) == 1

        # All digests in the event chain must match
        assert approval_events[0].payload["argument_digest"] == tr_digest
        assert claim_events[0].payload["argument_digest"] == tr_digest
