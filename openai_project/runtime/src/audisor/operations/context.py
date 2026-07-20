"""Canonical operation context for the Audisor executor.

This is the internal representation used by AudisorOperationExecutor.
Host adapters produce AuthorityContext and IdempotencyContext from
audisor.schemas; the executor converts them into this canonical form.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Mapping

from audisor.schemas.authority import CanonicalAuthority
from audisor.schemas.idempotency import IdempotencyContext
from audisor.schemas.operation import OperationConstraints


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