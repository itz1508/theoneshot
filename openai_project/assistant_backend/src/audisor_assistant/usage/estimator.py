"""Explicit approximate estimator for the current text-only payload."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING

from .models import Confidence, MeasurementSource, PreparedCompletionRequest, UsageEstimate


@dataclass(frozen=True)
class ApproximateTokenEstimator:
    chars_per_token: Decimal = Decimal("4")
    message_overhead_tokens: int = 4

    def __post_init__(self) -> None:
        if self.chars_per_token <= 0:
            raise ValueError("chars_per_token must be positive")
        if self.message_overhead_tokens < 0:
            raise ValueError("message_overhead_tokens cannot be negative")

    def estimate(self, request: PreparedCompletionRequest) -> UsageEstimate:
        character_count = sum(len(message.content) for message in request.messages)
        content_tokens = int(
            (Decimal(character_count) / self.chars_per_token).to_integral_value(
                rounding=ROUND_CEILING
            )
        )
        input_tokens = content_tokens + (
            len(request.messages) * self.message_overhead_tokens
        )
        return UsageEstimate(
            input_tokens=input_tokens,
            reserved_output_tokens=request.max_output_tokens,
            estimated_total_tokens=input_tokens + request.max_output_tokens,
            source=MeasurementSource.APPROXIMATE,
            confidence=Confidence.APPROXIMATE,
            method="character_ratio",
            fallback_reason="provider_tokenizer_unavailable",
            context_limit=request.context_limit,
        )
