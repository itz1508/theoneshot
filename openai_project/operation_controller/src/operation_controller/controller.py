"""Operation Controller — the central lifecycle coordinator.

The controller accepts operations from any source (task, prepared plan, fix,
resume) and drives them through the state machine without importing runtime
internals directly. It delegates to adapters for each subsystem.

Usage:
    controller = OperationController()
    result = controller.accept(source_kind="task", prompt="fix the auth bug")
    # → state transitions: received → planning → plan_ready → reviewing ...

    # Resume a suspended operation:
    controller.resume(operation_id, resume_input={...})
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping, Protocol

from operation_controller.states import (
    InvalidTransition,
    OperationRecord,
    OperationState,
    SUSPENDED_STATES,
    TERMINAL_STATES,
)


# ---------------------------------------------------------------------------
# Adapter protocols (no direct imports from runtime/aflow/fix)
# ---------------------------------------------------------------------------


class PlanningAdapter(Protocol):
    """Creates a candidate plan from a raw task prompt."""

    def create_plan(self, prompt: str, context: dict[str, Any]) -> dict[str, Any]:
        """Return a candidate plan structure."""
        ...


class ReviewAdapter(Protocol):
    """Submits a plan for A-Flow review."""

    def review(self, plan: dict[str, Any], operation_id: str) -> dict[str, Any]:
        """Return review result with decision, findings, etc."""
        ...


class FulfilmentAdapter(Protocol):
    """Resolves findings from review."""

    def fulfil(self, findings: list[dict[str, Any]], plan: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        """Return fulfilment result."""
        ...


class ExecutionAdapter(Protocol):
    """Executes a reviewed and locked plan in sandbox."""

    def execute(self, operation_id: str, plan: dict[str, Any], authority: dict[str, Any]) -> dict[str, Any]:
        """Return execution result."""
        ...


class OperationStore(Protocol):
    """Persists operation records."""

    def save(self, record: OperationRecord) -> None: ...
    def load(self, operation_id: str) -> OperationRecord | None: ...
    def list_all(self) -> list[OperationRecord]: ...
    def list_by_state(self, state: OperationState) -> list[OperationRecord]: ...


# ---------------------------------------------------------------------------
# File-based operation store
# ---------------------------------------------------------------------------


class FileOperationStore:
    """JSON file-based implementation of OperationStore."""

    def __init__(self, root: Path):
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)

    def _path(self, operation_id: str) -> Path:
        return self._root / f"{operation_id}.json"

    def save(self, record: OperationRecord) -> None:
        data = asdict(record)
        data["state"] = record.state.value
        path = self._path(record.operation_id)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        tmp.replace(path)

    def load(self, operation_id: str) -> OperationRecord | None:
        path = self._path(operation_id)
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        data["state"] = OperationState(data["state"])
        return OperationRecord(**data)

    def list_all(self) -> list[OperationRecord]:
        results = []
        for path in sorted(self._root.glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            data["state"] = OperationState(data["state"])
            results.append(OperationRecord(**data))
        return results

    def list_by_state(self, state: OperationState) -> list[OperationRecord]:
        return [r for r in self.list_all() if r.state == state]


# ---------------------------------------------------------------------------
# Controller
# ---------------------------------------------------------------------------


class OperationControllerResult:
    """Result from a controller operation."""

    def __init__(self, operation_id: str, state: OperationState, detail: dict[str, Any] | None = None):
        self.operation_id = operation_id
        self.state = state
        self.detail = detail or {}

    def __repr__(self) -> str:
        return f"OperationControllerResult(id={self.operation_id!r}, state={self.state.value!r})"


class OperationController:
    """Central lifecycle coordinator for all operation types.

    Wraps existing runtime components (BuildExecutor, FixDispatcher, A-Flow)
    via adapter protocols. Does not import from audisor.* directly.
    """

    def __init__(
        self,
        store: OperationStore | None = None,
        planning_adapter: PlanningAdapter | None = None,
        review_adapter: ReviewAdapter | None = None,
        fulfilment_adapter: FulfilmentAdapter | None = None,
        execution_adapter: ExecutionAdapter | None = None,
        *,
        store_root: Path | None = None,
    ):
        if store is None:
            root = store_root or Path(".codex") / "operations"
            self._store: OperationStore = FileOperationStore(root)
        else:
            self._store = store
        self._planning = planning_adapter
        self._review = review_adapter
        self._fulfilment = fulfilment_adapter
        self._execution = execution_adapter

    def accept(
        self,
        source_kind: str,
        prompt: str = "",
        *,
        plan: dict[str, Any] | None = None,
        operation_id: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> OperationControllerResult:
        """Accept a new operation from any source.

        Args:
            source_kind: "task" | "prepared_plan" | "fix" | "resume"
            prompt: Raw task description (for source_kind="task")
            plan: Pre-built plan (for source_kind="prepared_plan")
            operation_id: Optional caller-supplied ID
            context: Additional context for adapters

        Returns:
            OperationControllerResult with the operation's current state
        """
        op_id = operation_id or f"op-{uuid.uuid4().hex[:12]}"
        ctx = context or {}

        record = OperationRecord(
            operation_id=op_id,
            state=OperationState.RECEIVED,
            source_kind=source_kind,
            prompt=prompt,
        )
        self._store.save(record)

        # Route based on source kind
        if source_kind == "prepared_plan" and plan is not None:
            # Skip planning, go directly to review
            record.transition(OperationState.REVIEWING, reason="prepared_plan_submitted")
            record.artifacts["candidate_plan"] = json.dumps(plan)
            self._store.save(record)
            return self._do_review(record, plan, ctx)

        elif source_kind == "task":
            # Planning phase
            record.transition(OperationState.PLANNING, reason="task_accepted")
            self._store.save(record)
            return self._do_planning(record, prompt, ctx)

        elif source_kind == "fix":
            # Fix operations go directly to reviewing (plan is pre-qualified)
            record.transition(OperationState.REVIEWING, reason="fix_accepted")
            self._store.save(record)
            if plan:
                return self._do_review(record, plan, ctx)
            return OperationControllerResult(op_id, record.state, {"awaiting": "plan"})

        else:
            return OperationControllerResult(op_id, record.state, {"error": f"unknown source_kind: {source_kind}"})

    def resume(
        self,
        operation_id: str,
        resume_input: dict[str, Any] | None = None,
    ) -> OperationControllerResult:
        """Resume a suspended operation.

        Args:
            operation_id: The suspended operation to resume
            resume_input: Data required for resumption (varies by suspend reason)

        Returns:
            OperationControllerResult with the operation's new state
        """
        record = self._store.load(operation_id)
        if record is None:
            return OperationControllerResult(
                operation_id, OperationState.RECEIVED,
                {"error": "operation_not_found"},
            )

        if record.state not in SUSPENDED_STATES:
            return OperationControllerResult(
                operation_id, record.state,
                {"error": f"operation is not suspended (state={record.state.value})"},
            )

        # Determine resume target based on suspension type
        resume_target = self._resume_target(record)
        try:
            record.transition(resume_target, reason=f"resumed with input")
        except InvalidTransition as exc:
            return OperationControllerResult(
                operation_id, record.state,
                {"error": str(exc)},
            )
        self._store.save(record)

        return OperationControllerResult(operation_id, record.state, {"resumed": True})

    def cancel(self, operation_id: str, *, reason: str = "user_requested") -> OperationControllerResult:
        """Cancel an operation."""
        record = self._store.load(operation_id)
        if record is None:
            return OperationControllerResult(
                operation_id, OperationState.RECEIVED,
                {"error": "operation_not_found"},
            )

        if record.state in TERMINAL_STATES:
            return OperationControllerResult(
                operation_id, record.state,
                {"error": "operation already terminal"},
            )

        try:
            record.transition(OperationState.CANCELLED_BY_USER, reason=reason)
        except InvalidTransition as exc:
            return OperationControllerResult(
                operation_id, record.state,
                {"error": str(exc)},
            )
        self._store.save(record)
        return OperationControllerResult(operation_id, record.state)

    def status(self, operation_id: str) -> OperationControllerResult:
        """Get the current state of an operation."""
        record = self._store.load(operation_id)
        if record is None:
            return OperationControllerResult(
                operation_id, OperationState.RECEIVED,
                {"error": "operation_not_found"},
            )
        return OperationControllerResult(
            operation_id, record.state,
            {
                "source_kind": record.source_kind,
                "created_at": record.created_at,
                "updated_at": record.updated_at,
                "history_length": len(record.history),
                "suspend_reason": record.suspend_reason,
            },
        )

    def list_suspended(self) -> list[OperationControllerResult]:
        """List all operations currently in a suspended state."""
        results = []
        for state in SUSPENDED_STATES:
            for record in self._store.list_by_state(state):
                results.append(OperationControllerResult(
                    record.operation_id, record.state,
                    {"suspend_reason": record.suspend_reason},
                ))
        return results

    # ------------------------------------------------------------------
    # Internal phase handlers
    # ------------------------------------------------------------------

    def _do_planning(
        self, record: OperationRecord, prompt: str, context: dict[str, Any]
    ) -> OperationControllerResult:
        """Execute planning phase via adapter."""
        if self._planning is None:
            # No planning adapter — stay in PLANNING state awaiting external input
            return OperationControllerResult(
                record.operation_id, record.state,
                {"awaiting": "planning_adapter"},
            )

        plan = self._planning.create_plan(prompt, context)
        record.transition(OperationState.PLAN_READY, reason="plan_created")
        record.artifacts["candidate_plan"] = json.dumps(plan)
        self._store.save(record)

        # Auto-advance to review
        record.transition(OperationState.REVIEWING, reason="auto_review")
        self._store.save(record)
        return self._do_review(record, plan, context)

    def _do_review(
        self, record: OperationRecord, plan: dict[str, Any], context: dict[str, Any]
    ) -> OperationControllerResult:
        """Execute review phase via adapter."""
        if self._review is None:
            return OperationControllerResult(
                record.operation_id, record.state,
                {"awaiting": "review_adapter"},
            )

        result = self._review.review(plan, record.operation_id)
        decision = result.get("decision", "")

        if decision == "no_material_gap":
            # Clean review — advance to sandbox
            record.transition(OperationState.SANDBOX_READY, reason="review_clean")
            self._store.save(record)
            return OperationControllerResult(
                record.operation_id, record.state,
                {"review_decision": decision},
            )

        elif decision == "material_gap_found":
            # Needs fulfilment
            findings = result.get("findings", [])
            record.transition(OperationState.FULFILLING, reason="material_gap_found")
            self._store.save(record)
            return self._do_fulfilment(record, findings, plan, context)

        else:
            # Suspension for evidence or plan revision
            record.transition(
                OperationState.SUSPENDED_EVIDENCE,
                reason=f"review_decision={decision}",
            )
            record.suspend_reason = decision
            self._store.save(record)
            return OperationControllerResult(
                record.operation_id, record.state,
                {"review_decision": decision, "findings": result.get("findings", [])},
            )

    def _do_fulfilment(
        self,
        record: OperationRecord,
        findings: list[dict[str, Any]],
        plan: dict[str, Any],
        context: dict[str, Any],
    ) -> OperationControllerResult:
        """Execute fulfilment phase via adapter."""
        if self._fulfilment is None:
            return OperationControllerResult(
                record.operation_id, record.state,
                {"awaiting": "fulfilment_adapter", "findings_count": len(findings)},
            )

        result = self._fulfilment.fulfil(findings, plan, context)
        if result.get("all_resolved"):
            # Re-review with revised plan
            revised_plan = result.get("revised_plan", plan)
            record.transition(OperationState.REVIEWING, reason="fulfilment_complete")
            self._store.save(record)
            return self._do_review(record, revised_plan, context)
        else:
            # Needs more input
            unresolved = result.get("unresolved", [])
            if any(f.get("needs_decision") for f in unresolved):
                record.transition(OperationState.AWAITING_DECISION, reason="decision_required")
            else:
                record.transition(OperationState.COLLECTING_EVIDENCE, reason="evidence_required")
            self._store.save(record)
            return OperationControllerResult(
                record.operation_id, record.state,
                {"unresolved_count": len(unresolved)},
            )

    def _resume_target(self, record: OperationRecord) -> OperationState:
        """Determine the target state when resuming from suspension."""
        mapping = {
            OperationState.SUSPENDED_PLAN_REVISION: OperationState.REVIEWING,
            OperationState.SUSPENDED_PROVIDER_RECOVERY: OperationState.SANDBOX_READY,
            OperationState.SUSPENDED_EVIDENCE: OperationState.REVIEWING,
            OperationState.SUSPENDED_PACKAGE_REPAIR: OperationState.SANDBOX_READY,
            OperationState.SUSPENDED_DECISION: OperationState.REVIEWING,
        }
        return mapping.get(record.state, OperationState.RECEIVED)
