from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from audisor_assistant.usage import (
    AccountingStatus,
    AdmissionMode,
    ApproximateTokenEstimator,
    PreparedCompletionRequest,
    PreparedMessage,
    PricingRegistry,
    UsageAccountingService,
    UsageLedger,
)
from audisor_assistant.usage.normalizer import normalize_openai_usage

FIXTURE = (
    Path(__file__).parents[2]
    / "fixtures"
    / "usage"
    / "pricing_registry.v1.json"
)
NOW = datetime(2026, 7, 28, tzinfo=timezone.utc)


def _request(*, max_output_tokens: int = 20, context_limit: int = 100):
    return PreparedCompletionRequest(
        operation_id="op.service",
        attempt_id="attempt.001",
        provider="fixture-cloud-a",
        model="fixture-model-a",
        messages=(PreparedMessage(role="user", content="fixture request"),),
        max_output_tokens=max_output_tokens,
        context_limit=context_limit,
    )


def _service(tmp_path: Path) -> tuple[UsageAccountingService, UsageLedger]:
    ledger = UsageLedger(tmp_path / "ledger")
    service = UsageAccountingService(
        estimator=ApproximateTokenEstimator(),
        pricing_registry=PricingRegistry.from_path(FIXTURE),
        ledger=ledger,
        normalizer=lambda _provider, raw: normalize_openai_usage(raw),
    )
    return service, ledger


def test_observe_preflight_and_actual_finalization_are_separate(tmp_path: Path) -> None:
    request = _request()
    service, ledger = _service(tmp_path)

    preflight = service.preflight(request, at=NOW)
    result = service.finalize(
        request,
        preflight,
        {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
        at=NOW,
    )

    assert preflight.permitted is True
    assert preflight.estimate.input_tokens != result.actual.input_tokens
    assert result.actual.status is AccountingStatus.COMPLETE
    # Ruled behaviour: unknown cache categories stay None and block an
    # exact actual cost when the pricing record bills them.
    assert result.actual_cost is None
    assert result.warnings == (
        "cached_read tokens unknown but category is billable",
    )
    record = ledger.get(request.operation_id, request.attempt_id)
    assert record is not None and record.phase == "finalized"


def test_enforced_context_block_is_terminal_before_provider_work(tmp_path: Path) -> None:
    request = _request(max_output_tokens=20, context_limit=10)
    service, ledger = _service(tmp_path)

    preflight = service.preflight(
        request, at=NOW, context_mode=AdmissionMode.ENFORCE
    )

    assert preflight.permitted is False
    assert preflight.reasons == ("context_limit_exceeded",)
    record = ledger.get(request.operation_id, request.attempt_id)
    assert record is not None and record.phase == "blocked"


def test_enforced_cost_budget_blocks(tmp_path: Path) -> None:
    request = _request()
    service, _ledger = _service(tmp_path)
    preflight = service.preflight(
        request,
        at=NOW,
        cost_mode=AdmissionMode.ENFORCE,
        max_cost_usd=Decimal("0.000001"),
    )

    assert preflight.permitted is False
    assert preflight.reasons == ("usage_budget_exceeded",)


def test_missing_actual_usage_stays_incomplete(tmp_path: Path) -> None:
    request = _request()
    service, _ledger = _service(tmp_path)
    preflight = service.preflight(request, at=NOW)

    result = service.finalize(request, preflight, None, at=NOW)

    assert result.actual.status is AccountingStatus.INCOMPLETE
    assert result.actual_cost is None
    assert result.warnings == ("actual usage is incomplete",)
