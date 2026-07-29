"""Strict domain contracts for usage estimates, actuals, and pricing."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class MeasurementSource(str, Enum):
    PROVIDER_REPORTED = "provider_reported"
    LOCAL_EXACT = "local_exact"
    PROVIDER_COMPATIBLE = "provider_compatible"
    APPROXIMATE = "approximate"
    UNAVAILABLE = "unavailable"


class Confidence(str, Enum):
    EXACT = "exact"
    PROVIDER_COMPATIBLE = "provider_compatible"
    APPROXIMATE = "approximate"
    UNAVAILABLE = "unavailable"


class AccountingStatus(str, Enum):
    COMPLETE = "complete"
    INCOMPLETE = "incomplete"
    NOT_APPLICABLE = "not_applicable"


class PreparedMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    role: Literal["system", "user", "assistant", "tool"]
    content: str


class PreparedCompletionRequest(BaseModel):
    """Final provider payload representation used only for preflight work."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    operation_id: str = Field(min_length=1, max_length=128)
    attempt_id: str = Field(min_length=1, max_length=128)
    provider: str = Field(min_length=1, max_length=128)
    model: str = Field(min_length=1, max_length=256)
    messages: tuple[PreparedMessage, ...] = Field(min_length=1)
    max_output_tokens: int = Field(ge=0)
    context_limit: int | None = Field(default=None, ge=1)
    provider_options: dict[str, Any] = Field(default_factory=dict)

    def request_hash(self) -> str:
        payload = self.model_dump(
            mode="json", exclude={"operation_id", "attempt_id"}
        )
        encoded = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        return "sha256:" + hashlib.sha256(encoded).hexdigest()


class UsageEstimate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    input_tokens: int = Field(ge=0)
    reserved_output_tokens: int = Field(ge=0)
    estimated_total_tokens: int = Field(ge=0)
    source: MeasurementSource
    confidence: Confidence
    method: str = Field(min_length=1)
    fallback_reason: str | None = None
    context_limit: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def total_matches_parts(self) -> "UsageEstimate":
        if self.estimated_total_tokens != self.input_tokens + self.reserved_output_tokens:
            raise ValueError("estimated_total_tokens must equal input plus reserved output")
        return self


class NormalizedUsage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cached_read_tokens: int | None = Field(default=None, ge=0)
    cache_write_tokens: int | None = Field(default=None, ge=0)
    reasoning_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    source: MeasurementSource
    status: AccountingStatus

    @model_validator(mode="after")
    def status_matches_values(self) -> "NormalizedUsage":
        values = (
            self.input_tokens,
            self.output_tokens,
            self.cached_read_tokens,
            self.cache_write_tokens,
            self.reasoning_tokens,
            self.total_tokens,
        )
        if self.status is AccountingStatus.NOT_APPLICABLE and any(
            value is not None for value in values
        ):
            raise ValueError("not_applicable usage cannot contain token values")
        if self.status is AccountingStatus.COMPLETE:
            if self.input_tokens is None or self.output_tokens is None:
                raise ValueError("complete usage requires input and output tokens")
            expected = self.input_tokens + self.output_tokens
            if self.total_tokens != expected:
                raise ValueError("total_tokens must equal input plus output tokens")
        return self


class CategorySemantics(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    input_tokens: Literal["includes_cached_read", "excludes_cached_read"]
    reasoning_tokens: Literal["included_in_output", "separate", "not_reported"]


class PricingRates(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    uncached_input: Decimal | None = None
    cached_read: Decimal | None = None
    cached_write: Decimal | None = None
    output: Decimal | None = None
    reasoning: Decimal | None = None

    @field_validator("uncached_input", "cached_read", "cached_write", "output", "reasoning")
    @classmethod
    def non_negative_rate(cls, value: Decimal | None) -> Decimal | None:
        if value is not None and value < 0:
            raise ValueError("pricing rates cannot be negative")
        return value


class PricingRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    pricing_record_id: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    effective_from: datetime
    effective_until: datetime | None = None
    currency: Literal["USD"]
    billing_unit: Literal["per_1m_tokens"]
    category_semantics: CategorySemantics
    rates: PricingRates
    source_reference: str = Field(min_length=1)
    reviewed_at: datetime
    verification_status: Literal["reviewed", "fixture"]

    @model_validator(mode="after")
    def valid_window(self) -> "PricingRecord":
        if self.effective_until is not None and self.effective_until <= self.effective_from:
            raise ValueError("effective_until must be later than effective_from")
        return self


class PricingSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    pricing_record_id: str
    provider: str
    model: str
    effective_from: datetime
    effective_until: datetime | None = None
    currency: Literal["USD"]
    billing_unit: Literal["per_1m_tokens"]
    category_semantics: CategorySemantics
    rates: PricingRates
    source_reference: str
    reviewed_at: datetime
    verification_status: Literal["reviewed", "fixture"]
    snapshot_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class UsageCost(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    currency: Literal["USD"]
    uncached_input: Decimal
    cached_read: Decimal
    cached_write: Decimal
    output: Decimal
    reasoning: Decimal
    total: Decimal
    pricing_record_id: str
    pricing_snapshot_sha256: str
