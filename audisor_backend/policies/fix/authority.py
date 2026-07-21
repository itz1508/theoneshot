"""Parent-owned authority policy for Fix findings.

Defines which active scanner finding types require a human or business
authority decision, what decision is required, and how to evaluate
whether supplied decisions satisfy the requirements.

This is a product policy, not a fact established by the repository.
It uses exact active scanner finding type names.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from audisor_backend.schemas.fix.models import FindingsList


# ---------------------------------------------------------------------------
# Authority requirement: what a finding type needs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AuthorityRequirement:
    """Describes the authority decision required for one finding type."""

    finding_type: str
    decision_kind: str
    description: str
    allowed_options: tuple[str, ...] = ()
    # When True, the finding always requires a decision.
    # When False, the finding may be resolvable from repository evidence.
    always_required: bool = True


# ---------------------------------------------------------------------------
# Authority decision: a supplied resolution for one finding
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AuthorityDecision:
    """A user- or host-supplied authority decision for one finding."""

    finding_id: str
    decision_kind: str
    selected_value: str
    source: Literal["user", "host", "repository"] = "user"

    def to_mapping(self) -> dict[str, Any]:
        return {
            "finding_id": self.finding_id,
            "decision_kind": self.decision_kind,
            "selected_value": self.selected_value,
            "source": self.source,
        }

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "AuthorityDecision":
        return cls(
            finding_id=str(value["finding_id"]),
            decision_kind=str(value["decision_kind"]),
            selected_value=str(value["selected_value"]),
            source=value.get("source", "user"),
        )


# ---------------------------------------------------------------------------
# Authority evaluation: result of checking requirements against decisions
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AuthorityEvaluation:
    """Result of evaluating authority requirements against supplied decisions."""

    resolved_requirements: list[dict[str, Any]] = field(default_factory=list)
    unresolved_requirements: list[dict[str, Any]] = field(default_factory=list)
    status: Literal["pass", "decision_required"] = "pass"

    def to_mapping(self) -> dict[str, Any]:
        return {
            "resolved_requirements": self.resolved_requirements,
            "unresolved_requirements": self.unresolved_requirements,
            "status": self.status,
        }


# ---------------------------------------------------------------------------
# Parent-owned authority policy
# ---------------------------------------------------------------------------

AUTHORITY_POLICY: dict[str, AuthorityRequirement] = {
    "authority.competing_authority_path": AuthorityRequirement(
        finding_type="authority.competing_authority_path",
        decision_kind="select_authoritative_path",
        description="Select the authoritative path when multiple authority-named modules exist",
        always_required=True,
    ),
    "structure.duplicate_implementation": AuthorityRequirement(
        finding_type="structure.duplicate_implementation",
        decision_kind="select_canonical_implementation",
        description="Select the canonical implementation when repository evidence cannot establish one",
        always_required=False,
    ),
    "security.hardcoded_secret": AuthorityRequirement(
        finding_type="security.hardcoded_secret",
        decision_kind="confirm_credential_disposition",
        description="Code removal is implementation work; credential rotation or revocation remains an explicit external decision",
        always_required=True,
    ),
}


def authority_requirement(finding_type: str) -> AuthorityRequirement | None:
    """Return the authority requirement for a finding type, or None."""
    return AUTHORITY_POLICY.get(finding_type)


def evaluate_authority(
    findings: FindingsList,
    decisions: dict[str, AuthorityDecision],
) -> AuthorityEvaluation:
    """Evaluate whether supplied authority decisions satisfy all requirements.

    Args:
        findings: The accepted findings.
        decisions: Per-finding authority decisions keyed by finding_id.

    Returns:
        AuthorityEvaluation with resolved/unresolved requirements and status.
    """
    resolved: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []

    for finding in findings:
        requirement = authority_requirement(finding.type)
        if requirement is None:
            continue

        decision = decisions.get(finding.id)
        if decision is not None and decision.decision_kind == requirement.decision_kind:
            resolved.append({
                "finding_id": finding.id,
                "finding_type": finding.type,
                "decision_kind": requirement.decision_kind,
                "selected_value": decision.selected_value,
                "source": decision.source,
            })
        else:
            unresolved.append({
                "finding_id": finding.id,
                "finding_type": finding.type,
                "decision_kind": requirement.decision_kind,
                "description": requirement.description,
                "evidence": finding.evidence if isinstance(finding.evidence, dict) else {},
            })

    status: Literal["pass", "decision_required"] = (
        "decision_required" if unresolved else "pass"
    )
    return AuthorityEvaluation(
        resolved_requirements=resolved,
        unresolved_requirements=unresolved,
        status=status,
    )