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

    def review(
        self,
        plan: dict[str, Any],
        operation_id: str,
        provider_override: str | None = None,
    ) -> dict[str, Any]:
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
        event_store: Any | None = None,
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
        self._events = event_store

    def _emit(
        self,
        record: OperationRecord,
        event_type: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        if self._events is None:
            return
        self._events.append(
            record.operation_id,
            state=record.state.value,
            event_type=event_type,
            payload=payload or {},
        )
        # Always emit a companion state_transition event so the event store
        # captures every state the operation traversed.
        if event_type != "state_transition":
            self._events.append(
                record.operation_id,
                state=record.state.value,
                event_type="state_transition",
                payload={"to_state": record.state.value},
            )

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
        self._emit(record, "operation_created", {"source_kind": source_kind})

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
            self._emit(record, "state_transition", {"to_state": "planning"})
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

        resume_input = resume_input or {}
        resume_type = resume_input.get("resume_type", "")

        # Handle approval decisions
        if resume_type == "approval_decision":
            return self._handle_approval_decision(record, resume_input)

        # Handle tool results
        if resume_type == "tool_result":
            return self._handle_tool_result(record, resume_input)

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

        # Delegate to adapter for continuation if applicable
        if record.state == OperationState.PLANNING and self._planning:
            result = self._planning.resume_plan(record, resume_input)
            if result.get("status") == "suspended":
                return self._handle_adapter_result(record, result, "planning")
            # Planning completed — extract plan and advance
            plan = result.get("plan", result)
            record.transition(OperationState.PLAN_READY, reason="plan_resumed")
            record.artifacts["candidate_plan"] = json.dumps(plan)
            self._store.save(record)
            record.transition(OperationState.REVIEWING, reason="auto_review")
            self._store.save(record)
            return self._do_review(record, plan, context)
        elif record.state == OperationState.SANDBOX_RUNNING and self._execution:
            result = self._execution.resume_execution(record, resume_input)
            return self._handle_adapter_result(record, result, "execution")

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
        detail: dict[str, Any] = {
                "source_kind": record.source_kind,
                "created_at": record.created_at,
                "updated_at": record.updated_at,
                "history_length": len(record.history),
                "suspend_reason": record.suspend_reason,
            }
        # Expose suspension metadata when the operation is waiting for tool result
        if record.state == OperationState.SUSPENDED_FOR_TOOL_RESULT:
            detail["suspension_id"] = record.slices.get("suspension_id")
            detail["argument_digest"] = record.slices.get("argument_digest")
        return OperationControllerResult(
            operation_id, record.state, detail,
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

    def claim_tool_execution(
        self,
        operation_id: str,
        *,
        claimant_id: str,
        suspension_id: str,
        call_id: str,
        argument_digest: str,
    ) -> OperationControllerResult:
        """Atomically claim a pending tool execution for a specific claimant.

        This binds a tool-result suspension to a single claimant, preventing
        competing frontends from executing the same tool call. Returns the
        persisted canonical arguments for execution.
        """
        record = self._store.load(operation_id)
        if record is None:
            return OperationControllerResult(
                operation_id, OperationState.RECEIVED,
                {"error": "operation_not_found"},
            )

        # Must be suspended for tool result
        if record.state != OperationState.SUSPENDED_FOR_TOOL_RESULT:
            return OperationControllerResult(
                operation_id, record.state,
                {"error": "operation_not_suspended_for_tool_result"},
            )

        # Verify suspension ID matches
        persisted_suspension_id = record.slices.get("suspension_id", "")
        if suspension_id != persisted_suspension_id:
            return OperationControllerResult(
                operation_id, record.state,
                {"error": "suspension_id_mismatch"},
            )

        # Verify the pending call exists and matches
        pending_calls = record.slices.get("pending_calls", [])
        matching_call = None
        for pc in pending_calls:
            if pc.get("call_id") == call_id:
                matching_call = pc
                break

        if matching_call is None:
            return OperationControllerResult(
                operation_id, record.state,
                {"error": "tool_call_not_found"},
            )

        # Verify argument digest
        persisted_digest = record.slices.get("argument_digest", "")
        if persisted_digest and argument_digest != persisted_digest:
            return OperationControllerResult(
                operation_id, record.state,
                {"error": "argument_digest_mismatch"},
            )

        # Check for existing claim
        existing_claim = record.slices.get("execution_claim", {})
        if existing_claim:
            existing_claimant = existing_claim.get("claimant_id", "")
            if existing_claimant == claimant_id:
                # Idempotent — same claimant, same claim
                return OperationControllerResult(
                    operation_id, record.state,
                    {
                        "claimed": True,
                        "idempotent": True,
                        "claimant_id": claimant_id,
                        "call_id": call_id,
                        "tool_name": matching_call.get("tool_name", ""),
                        "arguments": matching_call.get("arguments", {}),
                        "argument_digest": persisted_digest,
                        "suspension_id": suspension_id,
                    },
                )
            else:
                # Competing claimant
                return OperationControllerResult(
                    operation_id, record.state,
                    {"error": "competing_claim",
                     "detail": f"already claimed by {existing_claimant}"},
                )

        # Persist the claim
        record.slices["execution_claim"] = {
            "claimant_id": claimant_id,
            "call_id": call_id,
            "suspension_id": suspension_id,
            "argument_digest": argument_digest,
        }
        self._store.save(record)

        # Emit claim event
        self._emit(record, "tool_execution_claimed", {
            "claimant_id": claimant_id,
            "call_id": call_id,
            "suspension_id": suspension_id,
            "argument_digest": argument_digest,
        })

        return OperationControllerResult(
            operation_id, record.state,
            {
                "claimed": True,
                "claimant_id": claimant_id,
                "call_id": call_id,
                "tool_name": matching_call.get("tool_name", ""),
                "arguments": matching_call.get("arguments", {}),
                "argument_digest": persisted_digest,
                "suspension_id": suspension_id,
            },
        )

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

        result = self._planning.create_plan(prompt, context)

        # Handle suspension results from the planning adapter
        if result.get("status") == "suspended":
            return self._handle_adapter_result(record, result, "planning")

        plan = result.get("plan", result)
        record.transition(OperationState.PLAN_READY, reason="plan_created")
        record.artifacts["candidate_plan"] = json.dumps(plan)
        self._store.save(record)

        # Auto-advance to review
        record.transition(OperationState.REVIEWING, reason="auto_review")
        self._store.save(record)
        return self._do_review(record, plan, context)

    def _do_review(
        self,
        record: OperationRecord,
        plan: dict[str, Any],
        context: dict[str, Any],
        *,
        provider_override: str | None = None,
    ) -> OperationControllerResult:
        """Execute review phase via adapter."""
        if self._review is None:
            return OperationControllerResult(
                record.operation_id, record.state,
                {"awaiting": "review_adapter"},
            )

        if provider_override is None:
            result = self._review.review(plan, record.operation_id)
        else:
            result = self._review.review(
                plan,
                record.operation_id,
                provider_override=provider_override,
            )
        decision = result.get("decision", "")

        if decision == "no_material_gap":
            # Clean review — advance to sandbox
            improved_plan = result.get("improved_plan")
            if isinstance(improved_plan, dict):
                record.artifacts["candidate_plan"] = json.dumps(improved_plan)
                record.artifacts["aflow_lifecycle_run_id"] = str(
                    result.get("review_id") or ""
                )
            record.transition(OperationState.SANDBOX_READY, reason="review_clean")
            self._store.save(record)
            self._emit(
                record,
                "aflow_review_completed",
                {"lifecycle_run_id": result.get("review_id")},
            )
            # Auto-advance to execution if adapter is available
            if self._execution is not None:
                return self._do_execution(record, context)
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

        elif decision == "provider_error":
            record.transition(
                OperationState.SUSPENDED_PROVIDER_RECOVERY,
                reason="aflow_provider_error",
            )
            record.suspend_reason = "aflow_provider_error"
            if result.get("issue_id"):
                record.artifacts["aflow_issue_id"] = str(result["issue_id"])
            self._store.save(record)
            self._emit(
                record,
                "aflow_issue_created",
                {
                    "issue_id": result.get("issue_id"),
                    "lifecycle_run_id": result.get("review_id"),
                },
            )
            return OperationControllerResult(
                record.operation_id,
                record.state,
                {
                    "review_decision": decision,
                    "issue_id": result.get("issue_id"),
                    "error": result.get("error"),
                },
            )

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

    def retry_aflow(
        self,
        operation_id: str,
        *,
        operation_version: int,
        idempotency_key: str,
        provider: str,
    ) -> OperationControllerResult:
        """Retry a provider-recovery review through the owning controller."""
        record = self._store.load(operation_id)
        if record is None:
            return OperationControllerResult(
                operation_id, OperationState.RECEIVED, {"error": "operation_not_found"}
            )
        replay_key = f"aflow_retry:{idempotency_key}"
        if replay_key in record.artifacts:
            return OperationControllerResult(
                operation_id,
                record.state,
                {"replayed": True, "retry": json.loads(record.artifacts[replay_key])},
            )
        if record.state != OperationState.SUSPENDED_PROVIDER_RECOVERY:
            return OperationControllerResult(
                operation_id,
                record.state,
                {"error": "operation_not_in_provider_recovery"},
            )
        current_version = len(record.history)
        if operation_version != current_version:
            return OperationControllerResult(
                operation_id,
                record.state,
                {"error": "stale_operation_version", "current_version": current_version},
            )
        raw_plan = record.artifacts.get("candidate_plan")
        if not raw_plan:
            return OperationControllerResult(
                operation_id, record.state, {"error": "candidate_plan_missing"}
            )
        plan = json.loads(raw_plan)
        record.transition(OperationState.REVIEWING, reason=f"aflow_retry:{provider}")
        self._store.save(record)
        retried = self._do_review(
            record,
            plan,
            {"retry": True},
            provider_override=provider,
        )
        latest = self._store.load(operation_id) or record
        latest.artifacts[replay_key] = json.dumps(
            {"provider": provider, "state": retried.state.value, **retried.detail}
        )
        self._store.save(latest)
        return retried

    def _do_execution(
        self, record: OperationRecord, context: dict[str, Any]
    ) -> OperationControllerResult:
        """Execute the approved plan via execution adapter."""
        if self._execution is None:
            return OperationControllerResult(
                record.operation_id, record.state,
                {"awaiting": "execution_adapter"},
            )

        raw_plan = record.artifacts.get("candidate_plan", "{}")
        plan = json.loads(raw_plan) if raw_plan else {}

        record.transition(OperationState.SANDBOX_RUNNING, reason="execution_started")
        self._store.save(record)

        result = self._execution.execute(record.operation_id, plan, context)
        return self._handle_adapter_result(record, result, "execution")

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

    def _handle_approval_decision(
        self, record: OperationRecord, resume_input: dict[str, Any]
    ) -> OperationControllerResult:
        """Handle approval decision (approve or deny)."""
        from operation_controller.approval import verify_argument_digest

        approved = resume_input.get("approved", False)
        incoming_suspension_id = resume_input.get("suspension_id", "")
        call_id = resume_input.get("call_id", "")

        # Verify suspension ID matches
        persisted_suspension_id = record.slices.get("suspension_id", "")
        approval_suspension_id = record.slices.get("approval_suspension_id", "")
        if incoming_suspension_id and (persisted_suspension_id or approval_suspension_id):
            if incoming_suspension_id != persisted_suspension_id and incoming_suspension_id != approval_suspension_id:
                return OperationControllerResult(
                    record.operation_id, record.state,
                    {"error": "suspension_id_mismatch", "detail": "approval for different suspension"},
                )

        # Check idempotency — already decided?
        if record.slices.get("approval_decided"):
            previous_decision = record.slices.get("approval_decision")
            if previous_decision == approved:
                # Idempotent replay — return current state
                return OperationControllerResult(
                    record.operation_id, record.state,
                    {"idempotent": True, "approved": approved},
                )
            else:
                # Conflicting decision
                return OperationControllerResult(
                    record.operation_id, record.state,
                    {"error": "conflicting_approval_decision",
                     "detail": f"already decided approved={previous_decision}"},
                )

        # Verify argument digest
        tool_call = record.slices.get("approval_tool_call", {})
        expected_digest = record.slices.get("argument_digest", "")
        if expected_digest and tool_call:
            tc_call_id = tool_call.get("call_id", call_id)
            tc_tool_name = tool_call.get("tool_name", "")
            tc_arguments = tool_call.get("arguments", {})
            if isinstance(tc_arguments, str):
                try:
                    tc_arguments = json.loads(tc_arguments)
                except (json.JSONDecodeError, TypeError):
                    tc_arguments = {}
            if not verify_argument_digest(tc_call_id, tc_tool_name, tc_arguments, expected_digest):
                return OperationControllerResult(
                    record.operation_id, record.state,
                    {"error": "argument_digest_mismatch",
                     "detail": "approved arguments differ from original request"},
                )

        # Process the decision
        if not approved:
            # Denial — cancel the operation
            try:
                record.transition(OperationState.CANCELLED_BY_USER, reason="approval_denied")
            except InvalidTransition as exc:
                return OperationControllerResult(
                    record.operation_id, record.state,
                    {"error": str(exc)},
                )
            record.slices["approval_decided"] = True
            record.slices["approval_decision"] = False
            self._store.save(record)
            self._emit(record, "tool_approval_denied", {
                "tool_call": tool_call,
                "call_id": tool_call.get("call_id", call_id),
                "tool_name": tool_call.get("tool_name", ""),
            })
            return OperationControllerResult(
                record.operation_id, record.state,
                {"approved": False, "reason": "approval_denied",
                 "tool_name": tool_call.get("tool_name", "")},
            )

        # Approved — two-stage: transition to tool-result suspension for frontend execution
        record.slices["approval_decided"] = True
        record.slices["approval_decision"] = True
        # Preserve the original approval suspension_id for idempotency checks
        record.slices["approval_suspension_id"] = record.slices.get("suspension_id", "")
        self._emit(record, "tool_approval_resolved", {
            "tool_call": tool_call,
            "call_id": tool_call.get("call_id", call_id),
            "approved": True,
        })

        # Integrate the approved tool result into the ToolLoop continuation
        pending_call = {
            "call_id": tool_call.get("call_id", call_id),
            "tool_name": tool_call.get("tool_name", ""),
            "arguments": tool_call.get("arguments", {}),
        }
        continuation_str = record.artifacts.get("continuation")
        if continuation_str:
            continuation = json.loads(continuation_str)
            # Add assistant message with the approved tool call
            args_str = json.dumps(pending_call["arguments"])
            continuation["messages"].append({
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": pending_call["call_id"],
                    "type": "function",
                    "function": {
                        "name": pending_call["tool_name"],
                        "arguments": args_str,
                    },
                }],
            })
            # Add synthetic tool result (approval granted, awaiting frontend execution)
            continuation["messages"].append({
                "role": "tool",
                "tool_call_id": pending_call["call_id"],
                "content": "approved",
            })
            # Clear pending raw calls — already integrated into messages
            continuation["pending_raw_calls"] = []
            record.artifacts["continuation"] = json.dumps(continuation)

        # Generate new suspension_id for tool-result suspension
        import secrets
        tool_result_suspension_id = f"susp-exec-{secrets.token_hex(4)}"
        record.slices["suspension_id"] = tool_result_suspension_id
        record.slices["pending_calls"] = [pending_call]

        # Transition from approval suspension to tool-result suspension
        try:
            record.transition(OperationState.SUSPENDED_FOR_TOOL_RESULT, reason="approval_granted")
        except InvalidTransition as exc:
            return OperationControllerResult(
                record.operation_id, record.state,
                {"error": str(exc)},
            )

        # Save AFTER transition so the persisted state is correct
        self._store.save(record)

        # Emit tool execution request for the frontend
        phase = record.slices.get("resume_phase", "execution")
        self._emit(record, "tool_execution_requested", {
            "pending_calls": [pending_call],
            "suspension_id": tool_result_suspension_id,
            "phase": phase,
            "argument_digest": record.slices.get("argument_digest", ""),
        })

        return OperationControllerResult(
            record.operation_id, record.state,
            {
                "approved": True,
                "suspended": True,
                "suspend_state": "suspended_for_tool_result",
                "pending_calls": [pending_call],
                "suspension_id": tool_result_suspension_id,
                "argument_digest": record.slices.get("argument_digest", ""),
            },
        )

    def _handle_tool_result(
        self, record: OperationRecord, resume_input: dict[str, Any]
    ) -> OperationControllerResult:
        """Handle tool result submission."""
        tool_results = resume_input.get("tool_results", [])

        # ── Claim verification ──
        execution_claim = record.slices.get("execution_claim", {})
        suspension_consumed = record.slices.get("suspension_consumed", False)

        if execution_claim:
            # This suspension was claimed — verify the result matches
            claimed_call_id = execution_claim.get("call_id", "")

            # Check for idempotent replay of already-consumed suspension
            if suspension_consumed:
                # Check if this is the same result (idempotent) or conflicting
                prev_results = record.slices.get("consumed_tool_results", [])
                if prev_results == tool_results:
                    # Idempotent replay — return current state
                    return OperationControllerResult(
                        record.operation_id, record.state,
                        {"idempotent": True, "resumed": True},
                    )
                else:
                    return OperationControllerResult(
                        record.operation_id, record.state,
                        {"error": "conflicting_result_replay",
                         "detail": "suspension already consumed with different results"},
                    )

            # Verify result call IDs match the claimed call
            for tr in tool_results:
                result_call_id = tr.get("call_id", "")
                if result_call_id and result_call_id != claimed_call_id:
                    return OperationControllerResult(
                        record.operation_id, record.state,
                        {"error": "result_call_id_mismatch",
                         "detail": f"result call_id {result_call_id} != claimed {claimed_call_id}"},
                    )

        # Transition to the appropriate running state
        resume_target = self._resume_target(record)
        try:
            record.transition(resume_target, reason="tool_result_submitted")
        except InvalidTransition as exc:
            return OperationControllerResult(
                record.operation_id, record.state,
                {"error": str(exc)},
            )

        # Mark suspension as consumed
        if execution_claim:
            record.slices["suspension_consumed"] = True
            record.slices["consumed_tool_results"] = tool_results

        self._store.save(record)

        # Resume planning adapter if we transitioned to PLANNING
        if record.state == OperationState.PLANNING and self._planning:
            result = self._planning.resume_plan(record, resume_input)
            if result.get("status") == "suspended":
                return self._handle_adapter_result(record, result, "planning")
            # Planning completed — extract plan and advance
            plan = result.get("plan", result)
            record.transition(OperationState.PLAN_READY, reason="plan_resumed")
            record.artifacts["candidate_plan"] = json.dumps(plan)
            self._store.save(record)
            record.transition(OperationState.REVIEWING, reason="auto_review")
            self._store.save(record)
            return self._do_review(record, plan, {})

        # Resume execution with the tool results
        if record.state == OperationState.SANDBOX_RUNNING and self._execution:
            result = self._execution.resume_execution(record, resume_input)
            return self._handle_adapter_result(record, result, "execution")

        return OperationControllerResult(
            record.operation_id, record.state,
            {"resumed": True, "tool_results": tool_results},
        )

    def _resume_target(self, record: OperationRecord) -> OperationState:
        """Determine the target state when resuming from suspension."""
        # For tool-result and approval suspensions, resume to the phase that
        # was running when the suspension occurred.
        if record.state in (
            OperationState.SUSPENDED_FOR_TOOL_RESULT,
            OperationState.SUSPENDED_FOR_APPROVAL,
        ):
            phase = record.slices.get("resume_phase", "")
            if phase == "execution":
                return OperationState.SANDBOX_RUNNING
            elif phase == "planning":
                return OperationState.PLANNING
            # Fallback for approval (always execution-phase)
            if record.state == OperationState.SUSPENDED_FOR_APPROVAL:
                return OperationState.SANDBOX_RUNNING
            return OperationState.PLANNING

        mapping = {
            OperationState.SUSPENDED_PLAN_REVISION: OperationState.REVIEWING,
            OperationState.SUSPENDED_PROVIDER_RECOVERY: OperationState.SANDBOX_READY,
            OperationState.SUSPENDED_EVIDENCE: OperationState.REVIEWING,
            OperationState.SUSPENDED_PACKAGE_REPAIR: OperationState.SANDBOX_READY,
            OperationState.SUSPENDED_DECISION: OperationState.REVIEWING,
        }
        return mapping.get(record.state, OperationState.RECEIVED)

    def _handle_adapter_result(
        self,
        record: OperationRecord,
        result: dict[str, Any],
        phase: str,
    ) -> OperationControllerResult:
        """Process adapter result and handle suspensions appropriately."""
        status = result.get("status", "")

        if status == "completed":
            record.transition(OperationState.COMPLETED, reason="adapter_completed")
            self._store.save(record)
            self._emit(record, "operation_completed", {"output": result.get("output", "")})
            return OperationControllerResult(
                record.operation_id, record.state,
                {"output": result.get("output", ""), "usage": result.get("usage", [])},
            )

        elif status == "failed":
            record.transition(OperationState.CANCELLED_BY_USER, reason="adapter_failed")
            self._store.save(record)
            self._emit(record, "operation_failed", {"error": result.get("error", "")})
            return OperationControllerResult(
                record.operation_id, record.state,
                {"error": result.get("error", "")},
            )

        elif status == "suspended":
            suspend_state = result.get("suspend_state", "")
            suspension_id = result.get("suspension_id", "")

            if suspend_state == "suspended_for_approval":
                return self._handle_approval_suspension(record, result, phase, suspension_id)
            elif suspend_state == "suspended_for_tool_result":
                return self._handle_tool_result_suspension(record, result, phase, suspension_id)
            else:
                # Other suspension types
                record.suspend_reason = result.get("reason", phase)
                if "continuation" in result:
                    record.artifacts["continuation"] = result["continuation"]
                if "suspension_id" in result:
                    record.slices["suspension_id"] = result["suspension_id"]
                if "pending_calls" in result:
                    record.slices["pending_calls"] = result["pending_calls"]
                record.slices["resume_phase"] = phase
                self._store.save(record)
                return OperationControllerResult(
                    record.operation_id, record.state,
                    {"suspended": True, "reason": result.get("reason")},
                )

        return OperationControllerResult(
            record.operation_id, record.state,
            {"error": f"unknown adapter status: {status}"},
        )

    def _handle_approval_suspension(
        self,
        record: OperationRecord,
        result: dict[str, Any],
        phase: str,
        suspension_id: str,
    ) -> OperationControllerResult:
        """Handle approval-required tool suspension."""
        from operation_controller.approval import compute_argument_digest

        tool_call = result.get("tool_call", {})
        call_id = tool_call.get("call_id", "")
        tool_name = tool_call.get("tool_name", "")
        arguments = tool_call.get("arguments", {})
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except (json.JSONDecodeError, TypeError):
                arguments = {}

        # Compute canonical request digest
        digest = compute_argument_digest(call_id, tool_name, arguments)

        # Persist approval envelope
        record.slices["suspension_id"] = suspension_id
        record.slices["argument_digest"] = digest
        record.slices["approval_tool_call"] = tool_call
        record.slices["resume_phase"] = phase
        if "continuation" in result:
            record.artifacts["continuation"] = result["continuation"]

        # Transition to approval suspension
        record.transition(OperationState.SUSPENDED_FOR_APPROVAL, reason="approval_required")
        record.suspend_reason = result.get("reason", "approval_required")
        self._store.save(record)

        # Emit approval request event
        self._emit(record, "tool_approval_requested", {
            "tool_call": tool_call,
            "call_id": call_id,
            "tool_name": tool_name,
            "argument_digest": digest,
            "suspension_id": suspension_id,
            "reason": result.get("reason", ""),
            "risk_level": result.get("risk_level", "medium"),
        })

        return OperationControllerResult(
            record.operation_id, record.state,
            {
                "suspended": True,
                "suspend_state": "suspended_for_approval",
                "suspension_id": suspension_id,
                "argument_digest": digest,
                "tool_call": tool_call,
                "reason": result.get("reason", ""),
                "risk_level": result.get("risk_level", "medium"),
            },
        )

    def _handle_tool_result_suspension(
        self,
        record: OperationRecord,
        result: dict[str, Any],
        phase: str,
        suspension_id: str,
    ) -> OperationControllerResult:
        """Handle frontend tool execution suspension."""
        pending_calls = result.get("pending_calls", [])

        # Persist tool-result suspension
        record.slices["suspension_id"] = suspension_id
        record.slices["pending_calls"] = pending_calls
        record.slices["resume_phase"] = phase
        if "continuation" in result:
            record.artifacts["continuation"] = result["continuation"]

        # Transition to tool-result suspension
        record.transition(OperationState.SUSPENDED_FOR_TOOL_RESULT, reason="frontend_tool_execution")
        record.suspend_reason = "frontend_tool_execution"
        self._store.save(record)

        # Emit tool execution request event
        self._emit(record, "tool_execution_requested", {
            "pending_calls": pending_calls,
            "suspension_id": suspension_id,
            "phase": phase,
        })

        return OperationControllerResult(
            record.operation_id, record.state,
            {
                "suspended": True,
                "suspend_state": "suspended_for_tool_result",
                "suspension_id": suspension_id,
                "pending_calls": pending_calls,
            },
        )
