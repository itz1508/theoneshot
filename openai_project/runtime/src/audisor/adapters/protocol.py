"""Host adapter protocol for Audisor.

All host adapters (Codex, generic MCP, Responses-compatible, CLI) must
implement these protocols.  The core receives AudisorOperationRequest and
returns AudisorOperationResult; adapters translate to/from host formats.

Adapters must ONLY translate.  They must NOT:
- Execute operations
- Alter authority
- Weaken safety limits
- Inject host-specific fields into the canonical request
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Mapping, Protocol

from audisor.schemas.authority import AuthorityContext, CanonicalAuthority
from audisor.schemas.idempotency import IdempotencyContext, IdempotencyKey


@dataclass(frozen=True)
class HostCapabilities:
    """Capabilities advertised by a host adapter."""

    supports_streaming: bool = False
    supports_tools: bool = False
    supports_artifacts: bool = False
    max_request_size_bytes: int = 1_000_000
    max_response_size_bytes: int = 1_000_000
    supported_content_types: tuple[str, ...] = ("text/plain", "application/json")

    def to_mapping(self) -> dict[str, Any]:
        return {
            "supports_streaming": self.supports_streaming,
            "supports_tools": self.supports_tools,
            "supports_artifacts": self.supports_artifacts,
            "max_request_size_bytes": self.max_request_size_bytes,
            "max_response_size_bytes": self.max_response_size_bytes,
            "supported_content_types": list(self.supported_content_types),
        }


@dataclass(frozen=True)
class AudisorOperationRequest:
    """Canonical operation request consumed by the Audisor core.

    All host adapters must produce exactly this structure.  No adapter may
    add fields; host-specific data belongs in host_context.
    """

    operation_id: str
    mode: Literal["build", "fix", "analyze", "validate"]
    request: Mapping[str, Any]
    authority: AuthorityContext
    constraints: Mapping[str, Any]
    host_capabilities: HostCapabilities
    host_context: Mapping[str, Any] = field(default_factory=dict)
    idempotency: IdempotencyContext | None = None

    def __post_init__(self) -> None:
        if not self.operation_id or not isinstance(self.operation_id, str):
            raise ValueError("operation_id must be a non-empty string")
        if not self.request:
            raise ValueError("request must be non-empty")


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
    error: Mapping[str, Any] | None = None
    idempotency_replay: bool = False
    idempotency_replay_count: int = 0


class HostRequestAdapter(Protocol):
    """Translate a host-specific request into an AudisorOperationRequest."""

    def translate_request(
        self,
        host_request: Mapping[str, Any],
        *,
        host_identity: str = "unknown",
    ) -> AudisorOperationRequest:
        """Translate host request to canonical Audisor request.

        Must validate all inputs, reject malformed requests, and never
        inject host-specific fields into the canonical structure.
        """
        ...

    def detect_capabilities(self, host_request: Mapping[str, Any]) -> HostCapabilities:
        """Detect capabilities from the host request context."""
        ...


class HostResponseAdapter(Protocol):
    """Translate an AudisorOperationResult into a host-specific response."""

    def translate_result(
        self,
        result: AudisorOperationResult,
        *,
        host_capabilities: HostCapabilities | None = None,
    ) -> Mapping[str, Any]:
        """Translate canonical Audisor result to host response.

        Must preserve all semantic information; may restructure for
        host compatibility but must not drop findings, evidence, or errors.
        """
        ...

    def translate_error(
        self,
        error: Mapping[str, Any],
        *,
        host_capabilities: HostCapabilities | None = None,
    ) -> Mapping[str, Any]:
        """Translate a canonical error into host-specific error format."""
        ...