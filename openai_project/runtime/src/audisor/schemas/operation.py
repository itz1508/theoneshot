"""Canonical operation schemas for Audisor.

Defines the core data structures that flow through the AudisorOperationExecutor.
All host adapters must produce and consume these structures.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Mapping

from audisor.schemas.authority import AuthorityContext, CanonicalAuthority
from audisor.schemas.idempotency import IdempotencyContext


@dataclass(frozen=True)
class OperationConstraints:
    """Immutable constraints for an operation execution."""

    max_tokens: int = 4096
    timeout_seconds: float = 300.0
    allowed_modes: tuple[str, ...] = ("build", "fix", "analyze", "validate")
    require_authority_confirmation: bool = True
    require_idempotency: bool = True

    def to_mapping(self) -> dict[str, Any]:
        return {
            "max_tokens": self.max_tokens,
            "timeout_seconds": self.timeout_seconds,
            "allowed_modes": list(self.allowed_modes),
            "require_authority_confirmation": self.require_authority_confirmation,
            "require_idempotency": self.require_idempotency,
        }


@dataclass(frozen=True)
class OperationArtifact:
    """Reference to a produced artifact."""

    artifact_id: str
    artifact_type: Literal["file", "log", "report", "contract", "lock"]
    path: str | None = None
    content_hash: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_mapping(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "artifact_type": self.artifact_type,
            "path": self.path,
            "content_hash": self.content_hash,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class OperationEvidence:
    """Evidence collected during operation execution."""

    evidence_id: str
    evidence_type: Literal["analysis", "validation", "execution", "audit"]
    source: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    timestamp: str = ""

    def to_mapping(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "evidence_type": self.evidence_type,
            "source": self.source,
            "payload": dict(self.payload),
            "timestamp": self.timestamp,
        }


@dataclass(frozen=True)
class AudisorOperationContext:
    """Complete context for an Audisor operation execution.

    This is the canonical internal representation used by the executor.
    Host adapters produce AuthorityContext and IdempotencyContext;
    the executor converts and binds them into this canonical form.
    """

    operation_id: str
    mode: Literal["build", "fix", "analyze", "validate"]
    request: Mapping[str, Any]
    authority: CanonicalAuthority
    constraints: OperationConstraints
    idempotency: IdempotencyContext | None = None
    host_identity: str = "unknown"
    host_capabilities: Mapping[str, Any] = field(default_factory=dict)

    def to_mapping(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "operation_id": self.operation_id,
            "mode": self.mode,
            "request": dict(self.request),
            "authority": self.authority.to_mapping(),
            "constraints": self.constraints.to_mapping(),
            "host_identity": self.host_identity,
            "host_capabilities": dict(self.host_capabilities),
        }
        if self.idempotency is not None:
            result["idempotency"] = self.idempotency.to_mapping()
        return result