"""Fulfilment participant registry and protocol definitions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class FulfilmentResult:
    """Result of a participant's fulfilment attempt."""

    finding_id: str
    status: str  # "fulfilled" | "needs_human_decision" | "needs_external_tool" | "unfulfillable"
    correction: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)
    revised_section: str | None = None
    participant_id: str = ""


@runtime_checkable
class FulfilmentParticipant(Protocol):
    """Protocol for fulfilment participants.

    Each participant handles a specific class of findings and can either
    fulfil them (produce corrections) or declare them unfulfillable.
    """

    @property
    def participant_id(self) -> str:
        """Unique identifier for this participant."""
        ...

    def can_fulfil(self, finding: dict[str, Any], context: dict[str, Any]) -> bool:
        """Check whether this participant can handle the given finding."""
        ...

    def fulfil(
        self,
        finding: dict[str, Any],
        original_plan: dict[str, Any],
        context: dict[str, Any],
    ) -> FulfilmentResult:
        """Attempt to fulfil (correct) the finding.

        Returns a FulfilmentResult indicating success or failure.
        The participant MUST NOT modify the original plan in place.
        """
        ...


class ParticipantRegistry:
    """Discovers and invokes fulfilment participants by finding class.

    Participants register themselves for specific finding classes.
    When a finding needs fulfilment, the registry resolves the appropriate
    participant and delegates to it.
    """

    def __init__(self) -> None:
        self._routes: dict[str, FulfilmentParticipant] = {}
        self._default_participants: list[FulfilmentParticipant] = []

    def register(self, finding_class: str, participant: FulfilmentParticipant) -> None:
        """Register a participant for a specific finding class."""
        self._routes[finding_class] = participant

    def register_default(self, participant: FulfilmentParticipant) -> None:
        """Register a fallback participant for unmatched findings."""
        self._default_participants.append(participant)

    def resolve(self, finding_class: str) -> FulfilmentParticipant | None:
        """Find the participant responsible for a finding class."""
        return self._routes.get(finding_class)

    def resolve_for_finding(
        self, finding: dict[str, Any], context: dict[str, Any]
    ) -> FulfilmentParticipant | None:
        """Resolve participant by finding class, then check can_fulfil."""
        finding_class = finding.get("class", finding.get("type", "unknown"))
        participant = self._routes.get(finding_class)
        if participant and participant.can_fulfil(finding, context):
            return participant
        # Try defaults
        for p in self._default_participants:
            if p.can_fulfil(finding, context):
                return p
        return None

    @property
    def registered_classes(self) -> list[str]:
        """Return all registered finding classes."""
        return list(self._routes.keys())
