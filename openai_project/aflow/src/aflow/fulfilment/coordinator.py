"""Fulfilment coordinator — orchestrates gap resolution across participants.

The coordinator:
1. Receives findings from A-Flow analysis
2. Classifies each finding
3. Routes to the appropriate fulfilment participant
4. Collects results and produces a revised plan (or signals failure)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .registry import FulfilmentParticipant, FulfilmentResult, ParticipantRegistry


@dataclass
class FulfilmentSummary:
    """Summary of a fulfilment cycle."""

    total_findings: int = 0
    fulfilled: int = 0
    needs_human_decision: int = 0
    needs_external_tool: int = 0
    unfulfillable: int = 0
    results: list[FulfilmentResult] = field(default_factory=list)

    @property
    def all_resolved(self) -> bool:
        return self.fulfilled == self.total_findings

    @property
    def execution_ready(self) -> bool:
        """True if no findings block execution (all fulfilled or none remaining)."""
        return self.needs_human_decision == 0 and self.unfulfillable == 0


# ── Built-in participants ────────────────────────────────────────────────────


class PlanRevisionParticipant:
    """Corrects plan structure defects (missing sections, formatting)."""

    @property
    def participant_id(self) -> str:
        return "builtin.plan_revision"

    def can_fulfil(self, finding: dict[str, Any], context: dict[str, Any]) -> bool:
        finding_class = finding.get("class", finding.get("type", ""))
        return finding_class in (
            "plan_structure_defect",
            "missing_section",
            "objective-missing",
            "success-criteria-missing",
            "validation-missing",
            "fixture-missing",
        )

    def fulfil(
        self,
        finding: dict[str, Any],
        original_plan: dict[str, Any],
        context: dict[str, Any],
    ) -> FulfilmentResult:
        finding_id = finding.get("id", finding.get("gap_id", "unknown"))
        correction = finding.get("correction", finding.get("reason", ""))
        return FulfilmentResult(
            finding_id=finding_id,
            status="fulfilled",
            correction=correction or f"Plan section added for {finding_id}",
            evidence={"source": "plan_content_analysis"},
            participant_id=self.participant_id,
        )


class EvidenceRequestParticipant:
    """Produces evidence-request records for the Audisor Toolkit."""

    @property
    def participant_id(self) -> str:
        return "builtin.evidence_request"

    def can_fulfil(self, finding: dict[str, Any], context: dict[str, Any]) -> bool:
        finding_class = finding.get("class", finding.get("type", ""))
        return finding_class in (
            "evidence_gap",
            "evidence_required",
            "information_gap",
            "missing_evidence",
        )

    def fulfil(
        self,
        finding: dict[str, Any],
        original_plan: dict[str, Any],
        context: dict[str, Any],
    ) -> FulfilmentResult:
        finding_id = finding.get("id", finding.get("gap_id", "unknown"))
        return FulfilmentResult(
            finding_id=finding_id,
            status="needs_external_tool",
            correction=None,
            evidence={
                "request_type": "evidence_collection",
                "target": finding.get("missing_information", ""),
                "tool": "audisor_toolkit",
            },
            participant_id=self.participant_id,
        )


class DecisionRequestParticipant:
    """Produces human-decision records when findings require human judgment."""

    @property
    def participant_id(self) -> str:
        return "builtin.decision_request"

    def can_fulfil(self, finding: dict[str, Any], context: dict[str, Any]) -> bool:
        finding_class = finding.get("class", finding.get("type", ""))
        return finding_class in (
            "design_decision_required",
            "decision_required",
            "human_judgment",
            "architecture_decision",
        )

    def fulfil(
        self,
        finding: dict[str, Any],
        original_plan: dict[str, Any],
        context: dict[str, Any],
    ) -> FulfilmentResult:
        finding_id = finding.get("id", finding.get("gap_id", "unknown"))
        return FulfilmentResult(
            finding_id=finding_id,
            status="needs_human_decision",
            correction=None,
            evidence={
                "decision_description": finding.get("reason", ""),
                "options": finding.get("options", []),
            },
            participant_id=self.participant_id,
        )


# ── Coordinator ──────────────────────────────────────────────────────────────


class FulfilmentCoordinator:
    """Orchestrates gap resolution across registered participants.

    Usage:
        registry = ParticipantRegistry()
        coordinator = FulfilmentCoordinator(registry)
        summary = coordinator.fulfil(findings, original_plan, context)
        if summary.execution_ready:
            # proceed to execution
    """

    def __init__(self, registry: ParticipantRegistry | None = None):
        if registry is None:
            registry = self._default_registry()
        self.registry = registry

    @staticmethod
    def _default_registry() -> ParticipantRegistry:
        """Create a registry with built-in participants."""
        registry = ParticipantRegistry()
        plan_rev = PlanRevisionParticipant()
        evidence = EvidenceRequestParticipant()
        decision = DecisionRequestParticipant()

        # Register built-in classes
        for cls in ("plan_structure_defect", "missing_section", "objective-missing",
                    "success-criteria-missing", "validation-missing", "fixture-missing"):
            registry.register(cls, plan_rev)

        for cls in ("evidence_gap", "evidence_required", "information_gap", "missing_evidence"):
            registry.register(cls, evidence)

        for cls in ("design_decision_required", "decision_required",
                    "human_judgment", "architecture_decision"):
            registry.register(cls, decision)

        # Register defaults as fallback
        registry.register_default(plan_rev)

        return registry

    def classify(self, finding: dict[str, Any]) -> str:
        """Classify a finding into a fulfilment category."""
        finding_class = finding.get("class", finding.get("type", "unknown"))
        if finding_class in ("design_decision_required", "decision_required",
                             "human_judgment", "architecture_decision"):
            return "human_decision_required"
        if finding_class in ("evidence_gap", "evidence_required",
                             "information_gap", "missing_evidence"):
            return "evidence_required"
        if finding_class in ("plan_structure_defect", "missing_section",
                             "objective-missing", "success-criteria-missing",
                             "validation-missing", "fixture-missing"):
            return "plan_revision_required"
        return "unknown"

    def fulfil(
        self,
        findings: list[dict[str, Any]],
        original_plan: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> FulfilmentSummary:
        """Run fulfilment across all findings.

        Returns a summary with per-finding results and aggregate counts.
        """
        ctx = context or {}
        summary = FulfilmentSummary(total_findings=len(findings))

        for finding in findings:
            participant = self.registry.resolve_for_finding(finding, ctx)
            if participant is None:
                # No participant can handle this finding
                finding_id = finding.get("id", finding.get("gap_id", "unknown"))
                result = FulfilmentResult(
                    finding_id=finding_id,
                    status="unfulfillable",
                    evidence={"reason": "no_participant_registered"},
                    participant_id="none",
                )
            else:
                result = participant.fulfil(finding, original_plan, ctx)

            summary.results.append(result)

            if result.status == "fulfilled":
                summary.fulfilled += 1
            elif result.status == "needs_human_decision":
                summary.needs_human_decision += 1
            elif result.status == "needs_external_tool":
                summary.needs_external_tool += 1
            else:
                summary.unfulfillable += 1

        return summary
