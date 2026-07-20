"""Idempotency contract for Audisor operations.

Every operation request must carry an idempotency key.  The core guarantees
that replaying a request with the same key within the retention window
produces the same result without side effects.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Annotated, Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator


class IdempotencyKey(BaseModel):
    """Validated idempotency key with optional scope binding."""

    model_config = ConfigDict(extra="forbid", strict=True)

    key: Annotated[str, Field(strict=True, min_length=1, max_length=256)]
    scope: Literal["operation", "task", "session", "global"] = "operation"
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))

    @field_validator("key")
    @classmethod
    def validate_key(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("idempotency key must be non-empty")
        # Reject keys that look like auto-generated UUIDs without context
        if len(value) == 32 and all(c in "0123456789abcdef" for c in value):
            raise ValueError("idempotency key must include semantic context, not bare hex")
        return value

    @field_validator("created_at")
    @classmethod
    def validate_timestamp(cls, value: str) -> str:
        if not value or "T" not in value:
            raise ValueError("created_at must be an ISO-8601 string")
        return value


class IdempotencyRecord(BaseModel):
    """Persisted record of an idempotent operation."""

    model_config = ConfigDict(extra="forbid", strict=True)

    key: str
    scope: str
    operation_id: str
    status: Literal["pending", "completed", "failed", "superseded"]
    result_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    created_at: str
    completed_at: str | None = None
    replay_count: int = Field(default=0, ge=0, le=1000)

    @field_validator("replay_count")
    @classmethod
    def validate_replay_count(cls, value: int) -> int:
        if value > 1000:
            raise ValueError("replay_count exceeds maximum allowed replays")
        return value


@dataclass(frozen=True)
class IdempotencyContext:
    """Immutable idempotency binding attached to an operation."""

    key: str
    scope: str
    operation_id: str
    first_seen_at: str

    @classmethod
    def from_request(cls, key: IdempotencyKey, operation_id: str) -> "IdempotencyContext":
        return cls(
            key=key.key,
            scope=key.scope,
            operation_id=operation_id,
            first_seen_at=key.created_at,
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "scope": self.scope,
            "operation_id": self.operation_id,
            "first_seen_at": self.first_seen_at,
        }


class IdempotencyStore:
    """In-memory idempotency store with configurable retention.

    Production implementations should replace this with a persistent
    backend (Redis, database, etc.).
    """

    DEFAULT_RETENTION_SECONDS: int = 86400  # 24 hours

    def __init__(self, retention_seconds: int = DEFAULT_RETENTION_SECONDS) -> None:
        self._retention = retention_seconds
        self._records: dict[str, IdempotencyRecord] = {}

    def _composite_key(self, key: str, scope: str) -> str:
        return f"{scope}:{key}"

    def check(self, idempotency_key: IdempotencyKey, operation_id: str) -> tuple[Literal["new", "replay", "conflict"], IdempotencyRecord | None]:
        """Check if an operation is new, a replay, or a conflict.

        Returns:
            - "new": No existing record; proceed with operation.
            - "replay": Existing record with same operation_id; return cached result.
            - "conflict": Existing record with different operation_id; reject.
        """
        composite = self._composite_key(idempotency_key.key, idempotency_key.scope)
        existing = self._records.get(composite)

        if existing is None:
            record = IdempotencyRecord(
                key=idempotency_key.key,
                scope=idempotency_key.scope,
                operation_id=operation_id,
                status="pending",
                result_hash="0" * 64,
                created_at=idempotency_key.created_at,
            )
            self._records[composite] = record
            return "new", record

        if existing.operation_id == operation_id:
            existing.replay_count += 1
            return "replay", existing

        return "conflict", existing

    def complete(self, idempotency_key: IdempotencyKey, operation_id: str, result_hash: str) -> None:
        """Mark an operation as completed with its result hash."""
        composite = self._composite_key(idempotency_key.key, idempotency_key.scope)
        record = self._records.get(composite)
        if record is None or record.operation_id != operation_id:
            raise ValueError("cannot complete unknown or mismatched operation")
        record.status = "completed"
        record.result_hash = result_hash
        record.completed_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    def fail(self, idempotency_key: IdempotencyKey, operation_id: str) -> None:
        """Mark an operation as failed."""
        composite = self._composite_key(idempotency_key.key, idempotency_key.scope)
        record = self._records.get(composite)
        if record is None or record.operation_id != operation_id:
            raise ValueError("cannot fail unknown or mismatched operation")
        record.status = "failed"
        record.completed_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")