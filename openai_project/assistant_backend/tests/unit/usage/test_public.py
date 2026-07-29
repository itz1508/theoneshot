import json
from datetime import datetime, timezone
from pathlib import Path

from audisor_assistant.usage import (
    AccountingStatus,
    ApproximateTokenEstimator,
    PreparedCompletionRequest,
    PreparedMessage,
    PricingRegistry,
    UsageAccountingEvidence,
    UsageAccountingService,
)
from audisor_assistant.usage.normalizer import normalize_anthropic_usage

FIXTURE = (
    Path(__file__).parents[2]
    / "fixtures"
    / "usage"
    / "pricing_registry.v1.json"
)
NOW = datetime(2026, 7, 28, tzinfo=timezone.utc)


def test_public_evidence_serializes_money_as_decimal_strings() -> None:
    request = PreparedCompletionRequest(
        operation_id="op.public",
        attempt_id="attempt.001",
        provider="fixture-cloud-a",
        model="fixture-model-a",
        messages=(PreparedMessage(role="user", content="private"),),
        max_output_tokens=10,
    )
    service = UsageAccountingService(
        estimator=ApproximateTokenEstimator(),
        pricing_registry=PricingRegistry.from_path(FIXTURE),
        normalizer=lambda _provider, raw: normalize_anthropic_usage(raw),
    )
    preflight = service.preflight(request, at=NOW)
    finalization = service.finalize(
        request,
        preflight,
        # Every billable category is reported, so the exact actual cost
        # is computable under the ruled unknown-blocks-billing rule.
        {
            "input_tokens": 10,
            "output_tokens": 2,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
        },
        at=NOW,
    )

    evidence = UsageAccountingEvidence.from_model_attempt(
        operation_id=request.operation_id,
        attempt_id=request.attempt_id,
        provider=request.provider,
        model=request.model,
        preflight=preflight,
        finalization=finalization,
    )
    payload = json.loads(evidence.model_dump_json())

    assert payload["accounting_status"] == "complete"
    assert isinstance(payload["actual_cost"]["total"], str)
    assert "private" not in evidence.model_dump_json()


def test_languagetool_is_explicitly_not_applicable() -> None:
    evidence = UsageAccountingEvidence.not_applicable(
        operation_id="op.lt",
        attempt_id="attempt.001",
        provider="languagetool",
    )

    assert evidence.accounting_status is AccountingStatus.NOT_APPLICABLE
    assert evidence.estimate is None
    assert evidence.actual is None
    assert evidence.actual_cost is None
