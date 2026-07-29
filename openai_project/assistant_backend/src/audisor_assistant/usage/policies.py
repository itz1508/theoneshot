"""Independent context and per-operation cost admission policies."""

from __future__ import annotations

from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, ConfigDict

from .models import Confidence, UsageCost, UsageEstimate


class AdmissionMode(str, Enum):
    OFF = "off"
    OBSERVE = "observe"
    WARN = "warn"
    ENFORCE = "enforce"


class AdmissionDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    permitted: bool
    reasons: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


def evaluate_context(
    estimate: UsageEstimate,
    *,
    mode: AdmissionMode,
    safety_margin_tokens: int = 0,
) -> AdmissionDecision:
    if safety_margin_tokens < 0:
        raise ValueError("safety_margin_tokens cannot be negative")
    if mode is AdmissionMode.OFF or estimate.context_limit is None:
        return AdmissionDecision(permitted=True)
    usable_context = max(0, estimate.context_limit - safety_margin_tokens)
    if estimate.estimated_total_tokens <= usable_context:
        return AdmissionDecision(permitted=True)
    reason = "context_limit_exceeded"
    if mode is AdmissionMode.ENFORCE:
        return AdmissionDecision(permitted=False, reasons=(reason,))
    if mode is AdmissionMode.WARN:
        return AdmissionDecision(permitted=True, warnings=(reason,))
    return AdmissionDecision(permitted=True, reasons=(reason,))


def evaluate_cost(
    estimate: UsageEstimate,
    estimated_cost: UsageCost | None,
    *,
    max_cost_usd: Decimal | None,
    mode: AdmissionMode,
    require_pricing: bool = False,
    allow_approximate: bool = True,
) -> AdmissionDecision:
    if max_cost_usd is not None and max_cost_usd < 0:
        raise ValueError("max_cost_usd cannot be negative")
    if mode is AdmissionMode.OFF or max_cost_usd is None:
        return AdmissionDecision(permitted=True)
    reasons: list[str] = []
    if estimate.confidence is Confidence.APPROXIMATE and not allow_approximate:
        reasons.append("approximate_count_not_allowed")
    if estimated_cost is None:
        if require_pricing:
            reasons.append("pricing_unavailable")
    elif estimated_cost.total > max_cost_usd:
        reasons.append("usage_budget_exceeded")
    if not reasons:
        return AdmissionDecision(permitted=True)
    if mode is AdmissionMode.ENFORCE:
        return AdmissionDecision(permitted=False, reasons=tuple(reasons))
    if mode is AdmissionMode.WARN:
        return AdmissionDecision(permitted=True, warnings=tuple(reasons))
    return AdmissionDecision(permitted=True, reasons=tuple(reasons))
