from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from audisor_assistant.usage import (
    AccountingStatus,
    MeasurementSource,
    NormalizedUsage,
    PricingRegistry,
    UsageCalculationError,
    calculate_actual_charge,
    calculate_estimated_charge,
)
from audisor_assistant.usage.models import UsageEstimate

FIXTURE = (
    Path(__file__).parents[2]
    / "fixtures"
    / "usage"
    / "pricing_registry.v1.json"
)


def _pricing():
    snapshot = PricingRegistry.from_path(FIXTURE).resolve(
        provider="fixture-cloud-a",
        model="fixture-model-a",
        at=datetime(2026, 7, 28, tzinfo=timezone.utc),
    )
    assert snapshot is not None
    return snapshot


def test_pricing_resolution_is_exact_and_snapshot_is_hashed() -> None:
    registry = PricingRegistry.from_path(FIXTURE)

    snapshot = registry.resolve(
        provider="fixture-cloud-a",
        model="fixture-model-a",
        at=datetime(2026, 7, 28, tzinfo=timezone.utc),
    )

    assert snapshot is not None
    assert snapshot.snapshot_sha256.startswith("sha256:")
    assert registry.resolve(
        provider="fixture-cloud-a",
        model="fixture-model-a-similar",
        at=datetime(2026, 7, 28, tzinfo=timezone.utc),
    ) is None


def test_cached_input_is_not_double_charged() -> None:
    usage = NormalizedUsage(
        input_tokens=2_000,
        output_tokens=300,
        cached_read_tokens=800,
        cache_write_tokens=0,
        reasoning_tokens=100,
        total_tokens=2_300,
        source=MeasurementSource.PROVIDER_REPORTED,
        status=AccountingStatus.COMPLETE,
    )

    cost = calculate_actual_charge(usage, _pricing())

    assert cost.uncached_input == Decimal("0.002400")
    assert cost.cached_read == Decimal("0.000400")
    assert cost.output == Decimal("0.002400")
    assert cost.reasoning == Decimal("0.000000")
    assert cost.total == Decimal("0.005200")


def test_invalid_cached_subset_is_rejected() -> None:
    usage = NormalizedUsage(
        input_tokens=500,
        output_tokens=100,
        cached_read_tokens=800,
        total_tokens=600,
        source=MeasurementSource.PROVIDER_REPORTED,
        status=AccountingStatus.COMPLETE,
    )

    with pytest.raises(UsageCalculationError, match="cached_read_tokens exceeds"):
        calculate_actual_charge(usage, _pricing())


def test_estimated_charge_uses_reserved_output_without_claiming_actual() -> None:
    estimate = UsageEstimate(
        input_tokens=1_200,
        reserved_output_tokens=300,
        estimated_total_tokens=1_500,
        source=MeasurementSource.APPROXIMATE,
        confidence="approximate",
        method="character_ratio",
        fallback_reason="provider_tokenizer_unavailable",
    )

    cost = calculate_estimated_charge(estimate, _pricing())

    assert cost.uncached_input == Decimal("0.002400")
    assert cost.output == Decimal("0.002400")
    assert cost.total == Decimal("0.004800")


def test_incomplete_actual_usage_has_no_calculated_charge() -> None:
    usage = NormalizedUsage(
        source=MeasurementSource.UNAVAILABLE,
        status=AccountingStatus.INCOMPLETE,
    )

    with pytest.raises(UsageCalculationError, match="incomplete"):
        calculate_actual_charge(usage, _pricing())
