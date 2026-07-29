"""Sanitized, versioned accounting envelope for API integration."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from .models import AccountingStatus, NormalizedUsage, UsageCost, UsageEstimate
from .policies import AdmissionDecision
from .service import UsageFinalization, UsagePreflight


class UsageAccountingEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0.0"] = "1.0.0"
    operation_id: str
    attempt_id: str
    provider: str
    model: str
    request_hash: str | None = None
    accounting_status: AccountingStatus
    estimate: UsageEstimate | None = None
    actual: NormalizedUsage | None = None
    estimated_cost: UsageCost | None = None
    actual_cost: UsageCost | None = None
    context_decision: AdmissionDecision | None = None
    cost_decision: AdmissionDecision | None = None
    pricing_snapshot_sha256: str | None = None
    pricing_record_id: str | None = None
    warnings: tuple[str, ...] = ()

    @classmethod
    def from_model_attempt(
        cls,
        *,
        operation_id: str,
        attempt_id: str,
        provider: str,
        model: str,
        preflight: UsagePreflight,
        finalization: UsageFinalization | None,
    ) -> "UsageAccountingEvidence":
        pricing = preflight.pricing_snapshot
        actual = finalization.actual if finalization is not None else None
        status = actual.status if actual is not None else AccountingStatus.INCOMPLETE
        return cls(
            operation_id=operation_id,
            attempt_id=attempt_id,
            provider=provider,
            model=model,
            request_hash=preflight.request_hash,
            accounting_status=status,
            estimate=preflight.estimate,
            actual=actual,
            estimated_cost=preflight.estimated_cost,
            actual_cost=(
                finalization.actual_cost if finalization is not None else None
            ),
            context_decision=preflight.context_decision,
            cost_decision=preflight.cost_decision,
            pricing_snapshot_sha256=(pricing.snapshot_sha256 if pricing else None),
            pricing_record_id=(pricing.pricing_record_id if pricing else None),
            warnings=preflight.warnings
            + (finalization.warnings if finalization is not None else ()),
        )

    @classmethod
    def not_applicable(
        cls,
        *,
        operation_id: str,
        attempt_id: str,
        provider: str,
        model: str = "not_applicable",
    ) -> "UsageAccountingEvidence":
        return cls(
            operation_id=operation_id,
            attempt_id=attempt_id,
            provider=provider,
            model=model,
            accounting_status=AccountingStatus.NOT_APPLICABLE,
        )
