"""Tests for the Operation Controller lifecycle state machine and routing."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from operation_controller.controller import (
    FileOperationStore,
    OperationController,
    OperationControllerResult,
)
from operation_controller.states import (
    InvalidTransition,
    OperationRecord,
    OperationState,
    SUSPENDED_STATES,
    TERMINAL_STATES,
    TRANSITIONS,
)


# ---------------------------------------------------------------------------
# State machine tests
# ---------------------------------------------------------------------------


class TestOperationRecord:
    def test_valid_transition_updates_state_and_history(self):
        record = OperationRecord(
            operation_id="op-1",
            state=OperationState.RECEIVED,
            source_kind="task",
        )
        record.transition(OperationState.PLANNING, reason="task_accepted")
        assert record.state == OperationState.PLANNING
        assert len(record.history) == 1
        assert record.history[0]["from"] == "received"
        assert record.history[0]["to"] == "planning"

    def test_invalid_transition_raises(self):
        record = OperationRecord(
            operation_id="op-1",
            state=OperationState.RECEIVED,
            source_kind="task",
        )
        with pytest.raises(InvalidTransition):
            record.transition(OperationState.COMPLETED)

    def test_terminal_state_rejects_all_transitions(self):
        record = OperationRecord(
            operation_id="op-1",
            state=OperationState.COMPLETED,
            source_kind="task",
        )
        with pytest.raises(InvalidTransition, match="terminal"):
            record.transition(OperationState.RECEIVED)

    def test_suspension_clears_on_resume(self):
        record = OperationRecord(
            operation_id="op-1",
            state=OperationState.SUSPENDED_PLAN_REVISION,
            source_kind="task",
            suspend_reason="needs_plan_revision",
        )
        record.transition(OperationState.REVIEWING, reason="resumed")
        assert record.suspend_reason is None

    def test_all_states_have_transitions_defined(self):
        for state in OperationState:
            if state not in TERMINAL_STATES:
                assert state in TRANSITIONS, f"No transitions defined for {state.value}"


# ---------------------------------------------------------------------------
# FileOperationStore tests
# ---------------------------------------------------------------------------


class TestFileOperationStore:
    def test_save_and_load(self, tmp_path: Path):
        store = FileOperationStore(tmp_path / "ops")
        record = OperationRecord(
            operation_id="op-test-1",
            state=OperationState.RECEIVED,
            source_kind="task",
            prompt="test prompt",
        )
        store.save(record)
        loaded = store.load("op-test-1")
        assert loaded is not None
        assert loaded.operation_id == "op-test-1"
        assert loaded.state == OperationState.RECEIVED
        assert loaded.prompt == "test prompt"

    def test_load_nonexistent_returns_none(self, tmp_path: Path):
        store = FileOperationStore(tmp_path / "ops")
        assert store.load("nonexistent") is None

    def test_list_all(self, tmp_path: Path):
        store = FileOperationStore(tmp_path / "ops")
        for i in range(3):
            record = OperationRecord(
                operation_id=f"op-{i}",
                state=OperationState.RECEIVED,
                source_kind="task",
            )
            store.save(record)
        all_ops = store.list_all()
        assert len(all_ops) == 3

    def test_list_by_state(self, tmp_path: Path):
        store = FileOperationStore(tmp_path / "ops")
        store.save(OperationRecord("op-1", OperationState.RECEIVED, "task"))
        store.save(OperationRecord("op-2", OperationState.PLANNING, "task"))
        store.save(OperationRecord("op-3", OperationState.RECEIVED, "task"))
        received = store.list_by_state(OperationState.RECEIVED)
        assert len(received) == 2


# ---------------------------------------------------------------------------
# Controller tests
# ---------------------------------------------------------------------------


class FakePlanningAdapter:
    def __init__(self, plan: dict[str, Any] | None = None):
        self.calls: list[tuple[str, dict]] = []
        self._plan = plan or {"plan_text": "test plan", "plan_id": "p-1"}

    def create_plan(self, prompt: str, context: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((prompt, context))
        return {"status": "completed", "plan": self._plan}

    def resume_plan(self, record: Any, resume_input: dict[str, Any]) -> dict[str, Any]:
        return {"status": "completed", "plan": self._plan}


class FakeReviewAdapter:
    def __init__(self, decision: str = "no_material_gap", findings: list | None = None):
        self.calls: list[tuple[dict, str]] = []
        self._decision = decision
        self._findings = findings or []

    def review(self, plan: dict[str, Any], operation_id: str) -> dict[str, Any]:
        self.calls.append((plan, operation_id))
        return {"decision": self._decision, "findings": self._findings}


class FakeFulfilmentAdapter:
    def __init__(self, resolved: bool = True):
        self.calls: list = []
        self._resolved = resolved

    def fulfil(self, findings: list, plan: dict, context: dict) -> dict[str, Any]:
        self.calls.append((findings, plan, context))
        if self._resolved:
            return {"all_resolved": True, "revised_plan": plan}
        return {"all_resolved": False, "unresolved": [{"id": "f-1", "needs_decision": True}]}


class FakeExecutionAdapter:
    def __init__(self, *, suspend: bool = False):
        self.calls: list = []
        self._suspend = suspend

    def execute(self, operation_id: str, plan: dict, authority: dict) -> dict[str, Any]:
        self.calls.append((operation_id, plan, authority))
        if self._suspend:
            return {
                "status": "suspended",
                "suspend_state": "suspended_for_approval",
                "suspension_id": "susp-fake-01",
                "tool_call": {
                    "call_id": "tc-1",
                    "tool_name": "shell_exec",
                    "arguments": {"command": "echo ok"},
                },
            }
        return {"status": "completed", "output": {}}


class TestOperationController:
    def test_accept_task_without_adapters_is_rejected(self, tmp_path: Path):
        store = FileOperationStore(tmp_path / "ops")
        ctrl = OperationController(store=store)
        result = ctrl.accept(source_kind="task", prompt="fix the auth bug")
        assert result.detail.get("error") == "required_adapters_unavailable"
        assert "planning" in result.detail["missing_adapters"]
        assert "review" in result.detail["missing_adapters"]
        assert "execution" in result.detail["missing_adapters"]

    def test_accept_task_with_all_adapters_reaches_sandbox(self, tmp_path: Path):
        store = FileOperationStore(tmp_path / "ops")
        planning = FakePlanningAdapter()
        review = FakeReviewAdapter(decision="no_material_gap")
        execution = FakeExecutionAdapter(suspend=True)
        ctrl = OperationController(
            store=store, planning_adapter=planning, review_adapter=review,
            execution_adapter=execution,
        )
        result = ctrl.accept(source_kind="task", prompt="fix the auth bug")
        assert result.state == OperationState.SUSPENDED_FOR_APPROVAL
        assert len(planning.calls) == 1
        assert len(review.calls) == 1
        assert len(execution.calls) == 1

    def test_accept_prepared_plan_skips_planning(self, tmp_path: Path):
        store = FileOperationStore(tmp_path / "ops")
        review = FakeReviewAdapter(decision="no_material_gap")
        execution = FakeExecutionAdapter(suspend=True)
        ctrl = OperationController(store=store, review_adapter=review, execution_adapter=execution)
        result = ctrl.accept(
            source_kind="prepared_plan",
            plan={"plan_text": "already built", "plan_id": "p-2"},
        )
        assert result.state == OperationState.SUSPENDED_FOR_APPROVAL
        assert len(review.calls) == 1

    def test_material_gap_triggers_fulfilment(self, tmp_path: Path):
        store = FileOperationStore(tmp_path / "ops")
        review = FakeReviewAdapter(
            decision="material_gap_found",
            findings=[{"id": "f-1", "class": "evidence_gap"}],
        )
        fulfilment = FakeFulfilmentAdapter(resolved=False)
        execution = FakeExecutionAdapter()
        ctrl = OperationController(
            store=store, review_adapter=review, fulfilment_adapter=fulfilment,
            execution_adapter=execution,
        )
        result = ctrl.accept(
            source_kind="prepared_plan",
            plan={"plan_text": "test", "plan_id": "p-3"},
        )
        # Fulfilment could not resolve all → awaiting decision
        assert result.state == OperationState.AWAITING_DECISION
        assert len(fulfilment.calls) == 1

    def test_resume_suspended_operation(self, tmp_path: Path):
        store = FileOperationStore(tmp_path / "ops")
        record = OperationRecord(
            operation_id="op-suspended",
            state=OperationState.SUSPENDED_PLAN_REVISION,
            source_kind="task",
            suspend_reason="needs_plan_revision",
        )
        store.save(record)
        ctrl = OperationController(store=store)
        result = ctrl.resume("op-suspended", resume_input={"revised_plan": {}})
        assert result.state == OperationState.REVIEWING
        assert result.detail.get("resumed") is True

    def test_resume_non_suspended_returns_error(self, tmp_path: Path):
        store = FileOperationStore(tmp_path / "ops")
        record = OperationRecord("op-active", OperationState.PLANNING, "task")
        store.save(record)
        ctrl = OperationController(store=store)
        result = ctrl.resume("op-active")
        assert "error" in result.detail
        assert "not suspended" in result.detail["error"]

    def test_cancel_operation(self, tmp_path: Path):
        store = FileOperationStore(tmp_path / "ops")
        record = OperationRecord("op-cancel", OperationState.PLANNING, "task")
        store.save(record)
        ctrl = OperationController(store=store)
        result = ctrl.cancel("op-cancel", reason="user_changed_mind")
        assert result.state == OperationState.CANCELLED_BY_USER

    def test_cancel_terminal_returns_error(self, tmp_path: Path):
        store = FileOperationStore(tmp_path / "ops")
        record = OperationRecord("op-done", OperationState.COMPLETED, "task")
        store.save(record)
        ctrl = OperationController(store=store)
        result = ctrl.cancel("op-done")
        assert "error" in result.detail

    def test_status_returns_current_state(self, tmp_path: Path):
        store = FileOperationStore(tmp_path / "ops")
        record = OperationRecord("op-status", OperationState.REVIEWING, "prepared_plan")
        store.save(record)
        ctrl = OperationController(store=store)
        result = ctrl.status("op-status")
        assert result.state == OperationState.REVIEWING
        assert result.detail["source_kind"] == "prepared_plan"

    def test_list_suspended(self, tmp_path: Path):
        store = FileOperationStore(tmp_path / "ops")
        store.save(OperationRecord("op-s1", OperationState.SUSPENDED_EVIDENCE, "task", suspend_reason="needs_evidence"))
        store.save(OperationRecord("op-s2", OperationState.SUSPENDED_DECISION, "task", suspend_reason="needs_decision"))
        store.save(OperationRecord("op-a1", OperationState.PLANNING, "task"))
        ctrl = OperationController(store=store)
        suspended = ctrl.list_suspended()
        assert len(suspended) == 2
        ids = {r.operation_id for r in suspended}
        assert ids == {"op-s1", "op-s2"}
