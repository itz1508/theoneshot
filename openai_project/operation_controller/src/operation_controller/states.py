"""Operation lifecycle states.

Defines the canonical state machine for operations processed by the controller.
States flow as:

    received → planning → plan_ready → reviewing → fulfilling
    → collecting_evidence → awaiting_decision → sandbox_ready
    → sandbox_running → verifying → ready_to_apply → completed

Terminal states: completed | cancelled_by_user | superseded
Suspension states: suspended_* (resumable via OperationEnvelope)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class OperationState(str, Enum):
    """All possible states an operation can be in."""

    # Initial
    RECEIVED = "received"

    # Planning phase
    PLANNING = "planning"
    PLAN_READY = "plan_ready"

    # Review phase
    REVIEWING = "reviewing"
    FULFILLING = "fulfilling"
    COLLECTING_EVIDENCE = "collecting_evidence"
    AWAITING_DECISION = "awaiting_decision"

    # Execution phase
    SANDBOX_READY = "sandbox_ready"
    SANDBOX_RUNNING = "sandbox_running"
    VERIFYING = "verifying"
    READY_TO_APPLY = "ready_to_apply"

    # Terminal states
    COMPLETED = "completed"
    CANCELLED_BY_USER = "cancelled_by_user"
    SUPERSEDED = "superseded"

    # Suspension states (resumable)
    SUSPENDED_PLAN_REVISION = "suspended_plan_revision"
    SUSPENDED_PROVIDER_RECOVERY = "suspended_provider_recovery"
    SUSPENDED_EVIDENCE = "suspended_evidence"
    SUSPENDED_PACKAGE_REPAIR = "suspended_package_repair"
    SUSPENDED_DECISION = "suspended_decision"
    SUSPENDED_FOR_APPROVAL = "suspended_for_approval"
    SUSPENDED_FOR_TOOL_RESULT = "suspended_for_tool_result"


# States that are terminal (no transitions out)
TERMINAL_STATES = frozenset({
    OperationState.COMPLETED,
    OperationState.CANCELLED_BY_USER,
    OperationState.SUPERSEDED,
})

# States that are suspended (resumable)
SUSPENDED_STATES = frozenset({
    OperationState.SUSPENDED_PLAN_REVISION,
    OperationState.SUSPENDED_PROVIDER_RECOVERY,
    OperationState.SUSPENDED_EVIDENCE,
    OperationState.SUSPENDED_PACKAGE_REPAIR,
    OperationState.SUSPENDED_DECISION,
    OperationState.SUSPENDED_FOR_APPROVAL,
    OperationState.SUSPENDED_FOR_TOOL_RESULT,
})


# Valid state transitions
TRANSITIONS: dict[OperationState, frozenset[OperationState]] = {
    OperationState.RECEIVED: frozenset({OperationState.PLANNING, OperationState.REVIEWING}),
    OperationState.PLANNING: frozenset({OperationState.PLAN_READY, OperationState.SUSPENDED_PLAN_REVISION, OperationState.SUSPENDED_FOR_TOOL_RESULT, OperationState.SUSPENDED_FOR_APPROVAL, OperationState.CANCELLED_BY_USER}),
    OperationState.PLAN_READY: frozenset({OperationState.REVIEWING, OperationState.CANCELLED_BY_USER}),
    OperationState.REVIEWING: frozenset({OperationState.FULFILLING, OperationState.SANDBOX_READY, OperationState.SUSPENDED_EVIDENCE, OperationState.SUSPENDED_PLAN_REVISION, OperationState.SUSPENDED_PROVIDER_RECOVERY, OperationState.CANCELLED_BY_USER}),
    OperationState.FULFILLING: frozenset({OperationState.COLLECTING_EVIDENCE, OperationState.AWAITING_DECISION, OperationState.REVIEWING, OperationState.CANCELLED_BY_USER}),
    OperationState.COLLECTING_EVIDENCE: frozenset({OperationState.REVIEWING, OperationState.SUSPENDED_EVIDENCE, OperationState.CANCELLED_BY_USER}),
    OperationState.AWAITING_DECISION: frozenset({OperationState.REVIEWING, OperationState.SUSPENDED_DECISION, OperationState.CANCELLED_BY_USER}),
    OperationState.SANDBOX_READY: frozenset({OperationState.SANDBOX_RUNNING, OperationState.SUSPENDED_PROVIDER_RECOVERY, OperationState.CANCELLED_BY_USER}),
    OperationState.SANDBOX_RUNNING: frozenset({OperationState.VERIFYING, OperationState.COMPLETED, OperationState.SUSPENDED_PROVIDER_RECOVERY, OperationState.SUSPENDED_PACKAGE_REPAIR, OperationState.SUSPENDED_FOR_APPROVAL, OperationState.SUSPENDED_FOR_TOOL_RESULT, OperationState.CANCELLED_BY_USER}),
    OperationState.VERIFYING: frozenset({OperationState.READY_TO_APPLY, OperationState.SANDBOX_READY, OperationState.CANCELLED_BY_USER}),
    OperationState.READY_TO_APPLY: frozenset({OperationState.COMPLETED, OperationState.CANCELLED_BY_USER}),
    # Suspended states can resume
    OperationState.SUSPENDED_PLAN_REVISION: frozenset({OperationState.PLANNING, OperationState.REVIEWING, OperationState.CANCELLED_BY_USER, OperationState.SUPERSEDED}),
    OperationState.SUSPENDED_PROVIDER_RECOVERY: frozenset({OperationState.REVIEWING, OperationState.SANDBOX_READY, OperationState.SANDBOX_RUNNING, OperationState.CANCELLED_BY_USER, OperationState.SUPERSEDED}),
    OperationState.SUSPENDED_EVIDENCE: frozenset({OperationState.COLLECTING_EVIDENCE, OperationState.REVIEWING, OperationState.CANCELLED_BY_USER, OperationState.SUPERSEDED}),
    OperationState.SUSPENDED_PACKAGE_REPAIR: frozenset({OperationState.SANDBOX_READY, OperationState.CANCELLED_BY_USER, OperationState.SUPERSEDED}),
    OperationState.SUSPENDED_DECISION: frozenset({OperationState.AWAITING_DECISION, OperationState.REVIEWING, OperationState.CANCELLED_BY_USER, OperationState.SUPERSEDED}),
    OperationState.SUSPENDED_FOR_APPROVAL: frozenset({OperationState.SANDBOX_RUNNING, OperationState.PLANNING, OperationState.SUSPENDED_FOR_TOOL_RESULT, OperationState.CANCELLED_BY_USER, OperationState.SUPERSEDED}),
    OperationState.SUSPENDED_FOR_TOOL_RESULT: frozenset({OperationState.SANDBOX_RUNNING, OperationState.PLANNING, OperationState.CANCELLED_BY_USER, OperationState.SUPERSEDED}),
}


@dataclass
class OperationRecord:
    """Persistent record of an operation's current state and history."""

    operation_id: str
    state: OperationState
    source_kind: str  # "task" | "prepared_plan" | "fix" | "resume"
    prompt: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    history: list[dict[str, Any]] = field(default_factory=list)
    suspend_reason: str | None = None
    resume_input_schema: dict[str, Any] | None = None
    artifacts: dict[str, str] = field(default_factory=dict)  # name → path
    slices: dict[str, Any] = field(default_factory=dict)

    def transition(self, new_state: OperationState, *, reason: str = "") -> None:
        """Transition to a new state with validation."""
        if self.state in TERMINAL_STATES:
            raise InvalidTransition(
                f"Cannot transition from terminal state {self.state.value}"
            )
        allowed = TRANSITIONS.get(self.state, frozenset())
        if new_state not in allowed:
            raise InvalidTransition(
                f"Invalid transition: {self.state.value} → {new_state.value}. "
                f"Allowed: {sorted(s.value for s in allowed)}"
            )
        self.history.append({
            "from": self.state.value,
            "to": new_state.value,
            "reason": reason,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        self.state = new_state
        self.updated_at = datetime.now(timezone.utc).isoformat()

        # Clear suspension metadata when leaving a suspended state
        if self.state not in SUSPENDED_STATES:
            self.suspend_reason = None
            self.resume_input_schema = None


class InvalidTransition(Exception):
    """Raised when an invalid state transition is attempted."""
