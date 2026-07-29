from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from audisor_assistant.usage import (
    AccountingStatus,
    AdmissionMode,
    ApproximateTokenEstimator,
    MeasurementSource,
    NormalizedUsage,
    PreparedCompletionRequest,
    PreparedMessage,
    PricingRegistry,
    UsageAttemptRecord,
    UsageLedger,
    UsageLedgerError,
    calculate_estimated_charge,
    evaluate_context,
    evaluate_cost,
)

FIXTURE = (
    Path(__file__).parents[2]
    / "fixtures"
    / "usage"
    / "pricing_registry.v1.json"
)


def _evidence():
    request = PreparedCompletionRequest(
        operation_id="op.fixture",
        attempt_id="attempt.001",
        provider="fixture-cloud-a",
        model="fixture-model-a",
        messages=(PreparedMessage(role="user", content="private fixture prompt"),),
        max_output_tokens=20,
        context_limit=30,
    )
    estimate = ApproximateTokenEstimator(message_overhead_tokens=0).estimate(request)
    pricing = PricingRegistry.from_path(FIXTURE).resolve(
        provider=request.provider,
        model=request.model,
        at=datetime(2026, 7, 28, tzinfo=timezone.utc),
    )
    assert pricing is not None
    return request, estimate, pricing


def test_context_enforcement_blocks_only_proven_overflow() -> None:
    _request, estimate, _pricing = _evidence()

    decision = evaluate_context(
        estimate, mode=AdmissionMode.ENFORCE, safety_margin_tokens=5
    )

    assert decision.permitted is False
    assert decision.reasons == ("context_limit_exceeded",)


def test_cost_policy_warns_without_blocking() -> None:
    _request, estimate, pricing = _evidence()
    estimated_cost = calculate_estimated_charge(estimate, pricing)

    decision = evaluate_cost(
        estimate,
        estimated_cost,
        max_cost_usd=Decimal("0.000001"),
        mode=AdmissionMode.WARN,
    )

    assert decision.permitted is True
    assert decision.warnings == ("usage_budget_exceeded",)


def test_strict_missing_pricing_blocks_in_enforce_mode() -> None:
    _request, estimate, _pricing = _evidence()

    decision = evaluate_cost(
        estimate,
        None,
        max_cost_usd=Decimal("1"),
        mode=AdmissionMode.ENFORCE,
        require_pricing=True,
    )

    assert decision.permitted is False
    assert decision.reasons == ("pricing_unavailable",)


def test_ledger_is_atomic_idempotent_and_private(tmp_path: Path) -> None:
    request, estimate, pricing = _evidence()
    ledger = UsageLedger(tmp_path / "ledger")
    reservation = UsageAttemptRecord(
        operation_id=request.operation_id,
        attempt_id=request.attempt_id,
        provider=request.provider,
        model=request.model,
        request_hash=request.request_hash(),
        phase="reserved",
        estimate=estimate,
        estimated_cost=calculate_estimated_charge(estimate, pricing),
        pricing_snapshot=pricing,
        created_at=datetime(2026, 7, 28, tzinfo=timezone.utc),
    )

    assert ledger.reserve(reservation) == reservation
    assert ledger.reserve(reservation) == reservation
    actual = NormalizedUsage(
        input_tokens=10,
        output_tokens=2,
        total_tokens=12,
        source=MeasurementSource.PROVIDER_REPORTED,
        status=AccountingStatus.COMPLETE,
    )
    finalized_at = datetime(2026, 7, 28, 0, 1, tzinfo=timezone.utc)
    final = ledger.finalize(
        operation_id=request.operation_id,
        attempt_id=request.attempt_id,
        actual=actual,
        actual_cost=None,
        finalized_at=finalized_at,
        warnings=("pricing_unavailable",),
    )

    assert ledger.finalize(
        operation_id=request.operation_id,
        attempt_id=request.attempt_id,
        actual=actual,
        actual_cost=None,
        finalized_at=finalized_at,
        warnings=("pricing_unavailable",),
    ) == final
    persisted = next((tmp_path / "ledger").glob("attempt-*.json")).read_text(
        encoding="utf-8"
    )
    assert "private fixture prompt" not in persisted
    assert '"phase":"finalized"' in persisted


def test_conflicting_finalization_is_rejected(tmp_path: Path) -> None:
    request, estimate, pricing = _evidence()
    ledger = UsageLedger(tmp_path)
    ledger.reserve(
        UsageAttemptRecord(
            operation_id=request.operation_id,
            attempt_id=request.attempt_id,
            provider=request.provider,
            model=request.model,
            request_hash=request.request_hash(),
            phase="reserved",
            estimate=estimate,
            pricing_snapshot=pricing,
            created_at=datetime(2026, 7, 28, tzinfo=timezone.utc),
        )
    )
    actual = NormalizedUsage(
        input_tokens=1,
        output_tokens=1,
        total_tokens=2,
        source=MeasurementSource.PROVIDER_REPORTED,
        status=AccountingStatus.COMPLETE,
    )
    ledger.finalize(
        operation_id=request.operation_id,
        attempt_id=request.attempt_id,
        actual=actual,
        actual_cost=None,
        finalized_at=datetime(2026, 7, 28, 0, 1, tzinfo=timezone.utc),
    )

    with pytest.raises(UsageLedgerError, match="conflicting"):
        ledger.finalize(
            operation_id=request.operation_id,
            attempt_id=request.attempt_id,
            actual=actual,
            actual_cost=None,
            finalized_at=datetime(2026, 7, 28, 0, 2, tzinfo=timezone.utc),
        )
