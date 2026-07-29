import pytest

from audisor_assistant.usage import AccountingStatus, UsageNormalizationError
from audisor_assistant.usage.normalizer import (
    normalize_anthropic_usage,
    normalize_openai_usage,
    normalize_provider_usage,
)


def test_openai_usage_preserves_cached_and_reasoning_categories() -> None:
    usage = normalize_openai_usage(
        {
            "prompt_tokens": 2_000,
            "completion_tokens": 300,
            "total_tokens": 2_300,
            "prompt_tokens_details": {"cached_tokens": 800},
            "completion_tokens_details": {"reasoning_tokens": 100},
        }
    )

    assert usage.status is AccountingStatus.COMPLETE
    assert usage.input_tokens == 2_000
    assert usage.output_tokens == 300
    assert usage.cached_read_tokens == 800
    assert usage.reasoning_tokens == 100
    assert usage.total_tokens == 2_300


def test_anthropic_usage_preserves_cache_categories() -> None:
    usage = normalize_anthropic_usage(
        {
            "input_tokens": 1_000,
            "output_tokens": 250,
            "cache_read_input_tokens": 400,
            "cache_creation_input_tokens": 50,
        }
    )

    assert usage.status is AccountingStatus.COMPLETE
    assert usage.cached_read_tokens == 400
    assert usage.cache_write_tokens == 50
    assert usage.total_tokens == 1_250


@pytest.mark.parametrize(
    "raw, message",
    [
        ({"prompt_tokens": -1, "completion_tokens": 1}, "prompt_tokens"),
        ({"prompt_tokens": 1.5, "completion_tokens": 1}, "prompt_tokens"),
        (
            {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 99},
            "total_tokens",
        ),
    ],
)
def test_malformed_usage_is_rejected(raw: dict, message: str) -> None:
    with pytest.raises(UsageNormalizationError, match=message):
        normalize_openai_usage(raw)


def test_missing_actual_usage_remains_incomplete() -> None:
    usage = normalize_openai_usage(None)

    assert usage.status is AccountingStatus.INCOMPLETE
    assert usage.input_tokens is None
    assert usage.total_tokens is None


def test_unknown_provider_mapping_is_not_guessed() -> None:
    with pytest.raises(UsageNormalizationError, match="unsupported provider"):
        normalize_provider_usage("similar-provider", {"prompt_tokens": 1})
