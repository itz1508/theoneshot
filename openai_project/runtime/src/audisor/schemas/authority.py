"""Canonical authority schema for all Audisor operations.

Every host adapter must produce an AuthorityContext that converges on this
schema.  The authority model is: who granted it, when, what permissions,
and what scope.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Annotated, Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class AuthoritySource(BaseModel):
    """Identifies the source of authority for an operation."""

    model_config = ConfigDict(extra="forbid", strict=True)

    source_type: Literal["user", "host_adapter", "system_policy", "mcp_server", "codex"]
    grant_id: Annotated[str, Field(strict=True, min_length=1, max_length=256)]
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))
    host_identity: str | None = None

    @field_validator("timestamp")
    @classmethod
    def validate_timestamp(cls, value: str) -> str:
        if not value or "T" not in value:
            raise ValueError("timestamp must be an ISO-8601 string")
        return value


class PermissionSet(BaseModel):
    """Mutable permissions granted for one operation."""

    model_config = ConfigDict(extra="forbid", strict=True)

    allowed_paths: list[Annotated[str, Field(strict=True, min_length=1, max_length=4096)]] = Field(
        default_factory=list, min_length=0, max_length=256
    )
    prohibited_paths: list[Annotated[str, Field(strict=True, min_length=1, max_length=4096)]] = Field(
        default_factory=list, min_length=0, max_length=256
    )
    allowed_tools: list[Annotated[str, Field(strict=True, min_length=1, max_length=128)]] = Field(
        default_factory=list, min_length=0, max_length=64
    )
    prohibited_tools: list[Annotated[str, Field(strict=True, min_length=1, max_length=128)]] = Field(
        default_factory=list, min_length=0, max_length=64
    )
    preserved_conditions: list[dict[str, Any]] = Field(default_factory=list, max_length=64)
    phase_order: list[str] | None = None

    @field_validator("allowed_paths", "prohibited_paths")
    @classmethod
    def validate_paths(cls, values: list[str]) -> list[str]:
        seen: set[str] = set()
        for value in values:
            normalized = value.replace("\\", "/").casefold()
            if normalized in seen:
                raise ValueError("paths must be unique")
            seen.add(normalized)
        return values

    @field_validator("allowed_tools", "prohibited_tools")
    @classmethod
    def validate_tools(cls, values: list[str]) -> list[str]:
        seen: set[str] = set()
        for value in values:
            normalized = value.casefold()
            if normalized in seen:
                raise ValueError("tool names must be unique")
            seen.add(normalized)
        return values

    @model_validator(mode="after")
    def validate_no_overlap(self) -> "PermissionSet":
        allowed_tools = {t.casefold() for t in self.allowed_tools}
        prohibited_tools = {t.casefold() for t in self.prohibited_tools}
        if allowed_tools & prohibited_tools:
            raise ValueError("allowed_tools and prohibited_tools must not overlap")
        allowed_paths = {p.replace("\\", "/").casefold() for p in self.allowed_paths}
        prohibited_paths = {p.replace("\\", "/").casefold() for p in self.prohibited_paths}
        if allowed_paths & prohibited_paths:
            raise ValueError("allowed_paths and prohibited_paths must not overlap")
        return self


class AuthorityContext(BaseModel):
    """Complete authority context for an Audisor operation.

    All host adapters must produce this exact structure.  No adapter may
    inject additional fields; translation must happen before construction.
    """

    model_config = ConfigDict(extra="forbid", strict=True)

    source: AuthoritySource
    permissions: PermissionSet
    scope: Annotated[str, Field(strict=True, min_length=1, max_length=4096)] = "repository"
    delegation_chain: list[AuthoritySource] = Field(default_factory=list, max_length=8)

    @field_validator("delegation_chain")
    @classmethod
    def validate_chain_length(cls, values: list[AuthoritySource]) -> list[AuthoritySource]:
        if len(values) > 8:
            raise ValueError("delegation chain must not exceed 8 entries")
        return values

    def to_mapping(self) -> dict[str, Any]:
        """Return a plain dict for legacy interfaces that expect Mapping."""
        return self.model_dump(mode="json")

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "AuthorityContext":
        """Construct from a plain dict, validating strictly."""
        if isinstance(value, cls):
            return value
        return cls.model_validate(dict(value))


@dataclass(frozen=True)
class CanonicalAuthority:
    """Immutable authority binding for an operation.

    This is the internal representation used by the Audisor core.  Host
    adapters produce AuthorityContext; the core converts it to this
    canonical form for storage and comparison.
    """

    source_type: str
    grant_id: str
    timestamp: str
    allowed_paths: tuple[str, ...]
    prohibited_paths: tuple[str, ...]
    allowed_tools: tuple[str, ...]
    prohibited_tools: tuple[str, ...]
    scope: str
    host_identity: str | None = None

    @classmethod
    def from_context(cls, context: AuthorityContext) -> "CanonicalAuthority":
        return cls(
            source_type=context.source.source_type,
            grant_id=context.source.grant_id,
            timestamp=context.source.timestamp,
            allowed_paths=tuple(context.permissions.allowed_paths),
            prohibited_paths=tuple(context.permissions.prohibited_paths),
            allowed_tools=tuple(context.permissions.allowed_tools),
            prohibited_tools=tuple(context.permissions.prohibited_tools),
            scope=context.scope,
            host_identity=context.source.host_identity,
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "source_type": self.source_type,
            "grant_id": self.grant_id,
            "timestamp": self.timestamp,
            "allowed_paths": list(self.allowed_paths),
            "prohibited_paths": list(self.prohibited_paths),
            "allowed_tools": list(self.allowed_tools),
            "prohibited_tools": list(self.prohibited_tools),
            "scope": self.scope,
            "host_identity": self.host_identity,
        }