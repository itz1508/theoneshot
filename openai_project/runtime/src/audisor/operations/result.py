"""Canonical operation result for Audisor.

Normalizes all operation outcomes into a single structure that all host
adapters translate into their host-specific response formats.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Mapping

from audisor.schemas.errors import AudisorError, AudisorRuntimeError


@dataclass(frozen=True)
class AudisorOperationResult:
    """Canonical operation result produced by the Audisor core.

    All host adapters must translate this into their host-specific
    response format without loss of semantic information.
    """

    operation_id: str
    status: Literal["accepted", "blocked", "failed", "completed", "pending", "replayed"]
    findings: list[Mapping[str, Any]] = field(default_factory=list)
    plan: Mapping[str, Any] | None = None
    execution: Mapping[str, Any] | None = None
    validation: Mapping[str, Any] | None = None
    evidence: list[Mapping[str, Any]] = field(default_factory=list)
    artifacts: list[Mapping[str, Any]] = field(default_factory=list)
    summary: str = ""
    error: AudisorError | None = None
    idempotency_replay: bool = False
    idempotency_replay_count: int = 0

    def to_mapping(self) -> dict[str, Any]:
        """Serialize to a plain dict for host adapter translation."""
        result: dict[str, Any] = {
            "operation_id": self.operation_id,
            "status": self.status,
            "findings": [dict(f) for f in self.findings],
            "evidence": [dict(e) for e in self.evidence],
            "artifacts": [dict(a) for a in self.artifacts],
            "summary": self.summary,
            "idempotency_replay": self.idempotency_replay,
            "idempotency_replay_count": self.idempotency_replay_count,
        }
        if self.plan is not None:
            result["plan"] = dict(self.plan)
        if self.execution is not None:
            result["execution"] = dict(self.execution)
        if self.validation is not None:
            result["validation"] = dict(self.validation)
        if self.error is not None:
            result["error"] = self.error.model_dump(mode="json")
        return result

    @classmethod
    def from_error(
        cls,
        operation_id: str,
        error: AudisorRuntimeError,
        *,
        status: Literal["blocked", "failed"] = "failed",
    ) -> "AudisorOperationResult":
        """Create a result from a runtime error."""
        return cls(
            operation_id=operation_id,
            status=status,
            error=error.to_error(),
            summary=f"Operation {status}: {error.message}",
        )

    @classmethod
    def from_success(
        cls,
        operation_id: str,
        *,
        findings: list[Mapping[str, Any]] | None = None,
        evidence: list[Mapping[str, Any]] | None = None,
        artifacts: list[Mapping[str, Any]] | None = None,
        summary: str = "",
        plan: Mapping[str, Any] | None = None,
        execution: Mapping[str, Any] | None = None,
        validation: Mapping[str, Any] | None = None,
    ) -> "AudisorOperationResult":
        """Create a successful completion result."""
        return cls(
            operation_id=operation_id,
            status="completed",
            findings=list(findings) if findings else [],
            evidence=list(evidence) if evidence else [],
            artifacts=list(artifacts) if artifacts else [],
            summary=summary,
            plan=plan,
            execution=execution,
            validation=validation,
        )

    @classmethod
    def replayed(
        cls,
        operation_id: str,
        replay_count: int,
        cached_result: Mapping[str, Any],
    ) -> "AudisorOperationResult":
        """Create a result indicating an idempotent replay."""
        return cls(
            operation_id=operation_id,
            status="replayed",
            idempotency_replay=True,
            idempotency_replay_count=replay_count,
            summary=f"Idempotent replay (count={replay_count})",
        )