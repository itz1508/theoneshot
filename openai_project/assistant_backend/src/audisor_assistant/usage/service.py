"""Observe-first orchestration for one model-provider attempt."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict

from .calculator import UsageCalculationError, calculate_actual_charge, calculate_estimated_charge
from .estimator import ApproximateTokenEstimator
from .ledger import UsageAttemptRecord, UsageLedger
from .models import NormalizedUsage, PreparedCompletionRequest, PricingSnapshot, UsageCost, UsageEstimate
from .normalizer import normalize_provider_usage, select_reply_usage
from .policies import AdmissionDecision, AdmissionMode, evaluate_context, evaluate_cost
from .pricing import PricingRegistry


class UsagePreflight(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    request_hash: str
    estimate: UsageEstimate
    pricing_snapshot: PricingSnapshot | None = None
    estimated_cost: UsageCost | None = None
    context_decision: AdmissionDecision
    cost_decision: AdmissionDecision

    @property
    def permitted(self) -> bool:
        return self.context_decision.permitted and self.cost_decision.permitted

    @property
    def reasons(self) -> tuple[str, ...]:
        return self.context_decision.reasons + self.cost_decision.reasons

    @property
    def warnings(self) -> tuple[str, ...]:
        return self.context_decision.warnings + self.cost_decision.warnings


class UsageFinalization(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    actual: NormalizedUsage
    actual_cost: UsageCost | None = None
    warnings: tuple[str, ...] = ()


class UsageAccountingService:
    def __init__(
        self,
        *,
        estimator: ApproximateTokenEstimator,
        pricing_registry: PricingRegistry,
        ledger: UsageLedger | None = None,
        normalizer: Callable[
            [str, Mapping[str, Any] | None], NormalizedUsage
        ] = normalize_provider_usage,
    ) -> None:
        self._estimator = estimator
        self._pricing_registry = pricing_registry
        self._ledger = ledger
        self._normalizer = normalizer

    def _persist_preflight(
        self,
        request: PreparedCompletionRequest,
        result: UsagePreflight,
        at: datetime,
    ) -> None:
        if self._ledger is None:
            return
        self._ledger.reserve(
            UsageAttemptRecord(
                operation_id=request.operation_id,
                attempt_id=request.attempt_id,
                provider=request.provider,
                model=request.model,
                request_hash=result.request_hash,
                phase="reserved",
                estimate=result.estimate,
                estimated_cost=result.estimated_cost,
                pricing_snapshot=result.pricing_snapshot,
                created_at=at,
                warnings=result.warnings,
            )
        )
        if not result.permitted:
            self._ledger.block(
                operation_id=request.operation_id,
                attempt_id=request.attempt_id,
                finalized_at=at,
                reasons=result.reasons,
            )

    def preflight(
        self,
        request: PreparedCompletionRequest,
        *,
        at: datetime,
        context_mode: AdmissionMode = AdmissionMode.OBSERVE,
        cost_mode: AdmissionMode = AdmissionMode.OFF,
        max_cost_usd: Decimal | None = None,
        safety_margin_tokens: int = 0,
        require_pricing: bool = False,
        allow_approximate: bool = True,
    ) -> UsagePreflight:
        estimate = self._estimator.estimate(request)
        pricing = self._pricing_registry.resolve(
            provider=request.provider, model=request.model, at=at
        )
        estimated_cost = (
            calculate_estimated_charge(estimate, pricing)
            if pricing is not None
            else None
        )
        result = UsagePreflight(
            request_hash=request.request_hash(),
            estimate=estimate,
            pricing_snapshot=pricing,
            estimated_cost=estimated_cost,
            context_decision=evaluate_context(
                estimate,
                mode=context_mode,
                safety_margin_tokens=safety_margin_tokens,
            ),
            cost_decision=evaluate_cost(
                estimate,
                estimated_cost,
                max_cost_usd=max_cost_usd,
                mode=cost_mode,
                require_pricing=require_pricing,
                allow_approximate=allow_approximate,
            ),
        )
        self._persist_preflight(request, result, at)
        return result

    def finalize(
        self,
        request: PreparedCompletionRequest,
        preflight: UsagePreflight,
        raw_usage: Mapping[str, Any] | None,
        *,
        at: datetime,
    ) -> UsageFinalization:
        actual = self._normalizer(request.provider, raw_usage)
        return self._finalize_normalized(request, preflight, actual, (), at=at)

    def finalize_from_reply(
        self,
        request: PreparedCompletionRequest,
        preflight: UsagePreflight,
        *,
        native_usage: Mapping[str, Any] | None,
        native_usage_invalid: bool,
        compat_usage: Mapping[str, Any] | None,
        at: datetime,
    ) -> UsageFinalization:
        """Finalize from a provider reply using the ruled source-selection
        order: valid native usage, labelled compatibility fallback, or an
        explicit ``native_usage_invalid`` with no silent fallback."""
        actual, selection_warnings = select_reply_usage(
            request.provider,
            native_usage=native_usage,
            native_usage_invalid=native_usage_invalid,
            compat_usage=compat_usage,
        )
        return self._finalize_normalized(
            request, preflight, actual, selection_warnings, at=at
        )

    def _finalize_normalized(
        self,
        request: PreparedCompletionRequest,
        preflight: UsagePreflight,
        actual: NormalizedUsage,
        selection_warnings: tuple[str, ...],
        *,
        at: datetime,
    ) -> UsageFinalization:
        warnings: list[str] = list(selection_warnings)
        actual_cost: UsageCost | None = None
        if preflight.pricing_snapshot is None:
            warnings.append("pricing_unavailable")
        else:
            try:
                actual_cost = calculate_actual_charge(
                    actual, preflight.pricing_snapshot
                )
            except UsageCalculationError as error:
                warnings.append(str(error))
        result = UsageFinalization(
            actual=actual, actual_cost=actual_cost, warnings=tuple(warnings)
        )
        if self._ledger is not None:
            self._ledger.finalize(
                operation_id=request.operation_id,
                attempt_id=request.attempt_id,
                actual=result.actual,
                actual_cost=result.actual_cost,
                finalized_at=at,
                warnings=result.warnings,
            )
        return result
