"""Canonical error contract for Audisor operations.

All errors across all adapters must converge on this schema.  Errors are
categorized by stage, retryability, and whether partial results are safe.

Also provides declared HTTP error contracts shared by FastAPI and schema
probes for backward compatibility.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Mapping, Union

from pydantic import BaseModel, ConfigDict, Field, RootModel, field_validator


# -----------------------------------------------------------------------------
# Canonical Audisor error schema (new, host-agnostic)
# -----------------------------------------------------------------------------


class AudisorErrorCode(BaseModel):
    """Structured error code with stage and retryability."""

    model_config = ConfigDict(extra="forbid", strict=True)

    category: Literal[
        "configuration",
        "validation",
        "authority",
        "provider",
        "network",
        "storage",
        "execution",
        "contract",
        "internal",
    ]
    stage: Literal[
        "request_translation",
        "authority_check",
        "idempotency_check",
        "plan_analysis",
        "model_invocation",
        "contract_assembly",
        "lock_creation",
        "execution",
        "validation",
        "result_translation",
    ]
    code: str
    retryable: bool = False
    max_retries: int = Field(default=0, ge=0, le=10)
    partial_result_safe: bool = False

    @field_validator("code")
    @classmethod
    def validate_code(cls, value: str) -> str:
        if not value or len(value) > 64:
            raise ValueError("code must be non-empty and <= 64 characters")
        if " " in value:
            raise ValueError("code must not contain spaces")
        return value

    @field_validator("max_retries")
    @classmethod
    def validate_max_retries(cls, value: int, info) -> int:
        if value > 0 and not info.data.get("retryable"):
            raise ValueError("max_retries > 0 requires retryable=True")
        return value


class AudisorErrorDetail(BaseModel):
    """Human-readable and machine-parseable error detail."""

    model_config = ConfigDict(extra="forbid", strict=True)

    message: str
    detail: str = ""
    suggested_action: str | None = None
    related_operation_id: str | None = None
    related_task_id: str | None = None

    @field_validator("message")
    @classmethod
    def validate_message(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("message must be non-empty")
        return value


class AudisorError(BaseModel):
    """Canonical error response for all Audisor operations."""

    model_config = ConfigDict(extra="forbid", strict=True)

    error_code: AudisorErrorCode
    error_detail: AudisorErrorDetail
    timestamp: str
    request_context: dict[str, Any] = Field(default_factory=dict)

    def to_exception(self) -> "AudisorRuntimeError":
        """Convert to a raiseable exception."""
        return AudisorRuntimeError(
            category=self.error_code.category,
            stage=self.error_code.stage,
            code=self.error_code.code,
            message=self.error_detail.message,
            detail=self.error_detail.detail,
            retryable=self.error_code.retryable,
            partial_result_safe=self.error_code.partial_result_safe,
            timestamp=self.timestamp,
        )


@dataclass(frozen=True)
class AudisorRuntimeError(RuntimeError):
    """Raiseable exception with canonical error fields."""

    category: str
    stage: str
    code: str
    message: str
    detail: str = ""
    retryable: bool = False
    partial_result_safe: bool = False
    timestamp: str = ""

    def __post_init__(self) -> None:
        if not self.timestamp:
            from datetime import datetime, timezone
            object.__setattr__(
                self,
                "timestamp",
                datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            )

    def __str__(self) -> str:
        return f"[{self.category}:{self.stage}:{self.code}] {self.message}"

    def to_error(self) -> AudisorError:
        """Convert to a serializable error model."""
        return AudisorError(
            error_code=AudisorErrorCode(
                category=self.category,
                stage=self.stage,
                code=self.code,
                retryable=self.retryable,
                partial_result_safe=self.partial_result_safe,
            ),
            error_detail=AudisorErrorDetail(
                message=self.message,
                detail=self.detail,
            ),
            timestamp=self.timestamp,
        )


class AudisorErrorResponse(BaseModel):
    """Standard error response envelope."""

    model_config = ConfigDict(extra="forbid", strict=True)

    status: Literal["error"] = "error"
    operation_id: str | None = None
    errors: list[AudisorError] = Field(min_length=1, max_length=16)
    partial_result: dict[str, Any] | None = None

    @field_validator("partial_result")
    @classmethod
    def validate_partial_result(cls, value: dict[str, Any] | None, info) -> dict[str, Any] | None:
        if value is not None:
            errors = info.data.get("errors", [])
            if not any(e.error_code.partial_result_safe for e in errors):
                raise ValueError("partial_result requires at least one error with partial_result_safe=True")
        return value


# -----------------------------------------------------------------------------
# Declared HTTP error contracts shared by FastAPI and schema probes
# (backward-compatible API response models)
# -----------------------------------------------------------------------------


class ErrorDetail(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    code: str
    message: str


class ErrorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    detail: ErrorDetail


class ValidationIssue(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: str
    loc: list[Union[str, int]]
    msg: str
    input: Any


class ValidationErrorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    detail: list[ValidationIssue]


class Declared422Response(RootModel[ErrorResponse | ValidationErrorResponse]):
    pass