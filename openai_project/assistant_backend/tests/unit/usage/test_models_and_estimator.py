from decimal import Decimal

import pytest
from pydantic import ValidationError

from audisor_assistant.usage import (
    ApproximateTokenEstimator,
    Confidence,
    MeasurementSource,
    PreparedCompletionRequest,
    PreparedMessage,
)


def _request(operation_id: str = "op.1", attempt_id: str = "attempt.1") -> PreparedCompletionRequest:
    return PreparedCompletionRequest(
        operation_id=operation_id,
        attempt_id=attempt_id,
        provider="fixture-cloud-a",
        model="fixture-model-a",
        messages=(
            PreparedMessage(role="system", content="12345678"),
            PreparedMessage(role="user", content="12345678"),
        ),
        max_output_tokens=10,
        context_limit=100,
    )


def test_request_hash_excludes_operation_and_attempt_identity() -> None:
    first = _request()
    second = _request(operation_id="op.2", attempt_id="attempt.9")

    assert first.request_hash() == second.request_hash()
    assert first.request_hash().startswith("sha256:")


def test_approximate_estimate_discloses_method_and_fallback() -> None:
    estimate = ApproximateTokenEstimator(
        chars_per_token=Decimal("4"), message_overhead_tokens=2
    ).estimate(_request())

    assert estimate.input_tokens == 8
    assert estimate.reserved_output_tokens == 10
    assert estimate.estimated_total_tokens == 18
    assert estimate.source is MeasurementSource.APPROXIMATE
    assert estimate.confidence is Confidence.APPROXIMATE
    assert estimate.method == "character_ratio"
    assert estimate.fallback_reason == "provider_tokenizer_unavailable"


def test_estimate_total_cannot_disagree_with_parts() -> None:
    with pytest.raises(ValidationError, match="estimated_total_tokens"):
        from audisor_assistant.usage.models import UsageEstimate

        UsageEstimate(
            input_tokens=4,
            reserved_output_tokens=2,
            estimated_total_tokens=99,
            source=MeasurementSource.APPROXIMATE,
            confidence=Confidence.APPROXIMATE,
            method="fixture",
        )
