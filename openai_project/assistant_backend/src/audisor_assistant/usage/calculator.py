"""Deterministic Decimal charge calculations."""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_EVEN

from .models import AccountingStatus, NormalizedUsage, PricingSnapshot, UsageCost, UsageEstimate

_MILLION = Decimal("1000000")
_PRECISION = Decimal("0.000001")


class UsageCalculationError(ValueError):
    pass


def _charge(tokens: int, rate: Decimal | None, category: str) -> Decimal:
    if tokens == 0:
        return Decimal("0.000000")
    if rate is None:
        raise UsageCalculationError(f"pricing unavailable for {category}")
    return (Decimal(tokens) * rate / _MILLION).quantize(
        _PRECISION, rounding=ROUND_HALF_EVEN
    )


def _cost(
    *,
    uncached_input_tokens: int,
    cached_read_tokens: int,
    cached_write_tokens: int,
    output_tokens: int,
    reasoning_tokens: int,
    pricing: PricingSnapshot,
) -> UsageCost:
    uncached_input = _charge(
        uncached_input_tokens, pricing.rates.uncached_input, "uncached_input"
    )
    cached_read = _charge(cached_read_tokens, pricing.rates.cached_read, "cached_read")
    cached_write = _charge(
        cached_write_tokens, pricing.rates.cached_write, "cached_write"
    )
    output = _charge(output_tokens, pricing.rates.output, "output")
    reasoning = _charge(reasoning_tokens, pricing.rates.reasoning, "reasoning")
    total = (uncached_input + cached_read + cached_write + output + reasoning).quantize(
        _PRECISION, rounding=ROUND_HALF_EVEN
    )
    return UsageCost(
        currency=pricing.currency,
        uncached_input=uncached_input,
        cached_read=cached_read,
        cached_write=cached_write,
        output=output,
        reasoning=reasoning,
        total=total,
        pricing_record_id=pricing.pricing_record_id,
        pricing_snapshot_sha256=pricing.snapshot_sha256,
    )


def _known_tokens(
    tokens: int | None, rate: Decimal | None, category: str
) -> int:
    """Unknown (None) token counts never become zero: when the category is
    billable under the pricing record, an unknown count prevents exact
    billing and must fail instead of silently under-charging."""
    if tokens is None:
        if rate is not None:
            raise UsageCalculationError(
                f"{category} tokens unknown but category is billable"
            )
        return 0
    return tokens


def calculate_actual_charge(
    usage: NormalizedUsage, pricing: PricingSnapshot
) -> UsageCost:
    if usage.status is not AccountingStatus.COMPLETE:
        raise UsageCalculationError("actual usage is incomplete")
    assert usage.input_tokens is not None
    assert usage.output_tokens is not None
    cached_read_tokens = _known_tokens(
        usage.cached_read_tokens, pricing.rates.cached_read, "cached_read"
    )
    if pricing.category_semantics.input_tokens == "includes_cached_read":
        if cached_read_tokens > usage.input_tokens:
            raise UsageCalculationError("cached_read_tokens exceeds input_tokens")
        uncached_input_tokens = usage.input_tokens - cached_read_tokens
    else:
        uncached_input_tokens = usage.input_tokens
    if pricing.category_semantics.reasoning_tokens == "included_in_output":
        reasoning_tokens = 0
    elif pricing.category_semantics.reasoning_tokens == "not_reported":
        if usage.reasoning_tokens:
            raise UsageCalculationError(
                "reasoning tokens contradict pricing semantics"
            )
        reasoning_tokens = 0
    else:
        reasoning_tokens = _known_tokens(
            usage.reasoning_tokens, pricing.rates.reasoning, "reasoning"
        )
    return _cost(
        uncached_input_tokens=uncached_input_tokens,
        cached_read_tokens=cached_read_tokens,
        cached_write_tokens=_known_tokens(
            usage.cache_write_tokens, pricing.rates.cached_write, "cached_write"
        ),
        output_tokens=usage.output_tokens,
        reasoning_tokens=reasoning_tokens,
        pricing=pricing,
    )


def calculate_estimated_charge(
    estimate: UsageEstimate, pricing: PricingSnapshot
) -> UsageCost:
    return _cost(
        uncached_input_tokens=estimate.input_tokens,
        cached_read_tokens=0,
        cached_write_tokens=0,
        output_tokens=estimate.reserved_output_tokens,
        reasoning_tokens=0,
        pricing=pricing,
    )
