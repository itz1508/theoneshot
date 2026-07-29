"""Provider-reported token normalization with strict validation."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .models import AccountingStatus, MeasurementSource, NormalizedUsage


class UsageNormalizationError(ValueError):
    pass


def _token(mapping: Mapping[str, Any], key: str) -> int | None:
    value = mapping.get(key)
    if value is None:
        return None
    if type(value) is not int or value < 0:
        raise UsageNormalizationError(f"{key} must be a non-negative integer")
    return value


def _details(raw: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = raw.get(key)
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise UsageNormalizationError(f"{key} must be an object")
    return value


def normalize_openai_usage(raw: Mapping[str, Any] | None) -> NormalizedUsage:
    if not raw:
        return NormalizedUsage(
            source=MeasurementSource.UNAVAILABLE,
            status=AccountingStatus.INCOMPLETE,
        )
    input_tokens = _token(raw, "prompt_tokens")
    if input_tokens is None:
        input_tokens = _token(raw, "input_tokens")
    output_tokens = _token(raw, "completion_tokens")
    if output_tokens is None:
        output_tokens = _token(raw, "output_tokens")
    if input_tokens is None or output_tokens is None:
        return NormalizedUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            source=MeasurementSource.PROVIDER_REPORTED,
            status=AccountingStatus.INCOMPLETE,
        )
    prompt_details = _details(raw, "prompt_tokens_details")
    completion_details = _details(raw, "completion_tokens_details")
    total_tokens = _token(raw, "total_tokens")
    expected_total = input_tokens + output_tokens
    if total_tokens is not None and total_tokens != expected_total:
        raise UsageNormalizationError("total_tokens does not equal input plus output")
    return NormalizedUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_read_tokens=_token(prompt_details, "cached_tokens"),
        reasoning_tokens=_token(completion_details, "reasoning_tokens"),
        total_tokens=expected_total,
        source=MeasurementSource.PROVIDER_REPORTED,
        status=AccountingStatus.COMPLETE,
    )


def normalize_anthropic_usage(raw: Mapping[str, Any] | None) -> NormalizedUsage:
    if not raw:
        return NormalizedUsage(
            source=MeasurementSource.UNAVAILABLE,
            status=AccountingStatus.INCOMPLETE,
        )
    input_tokens = _token(raw, "input_tokens")
    output_tokens = _token(raw, "output_tokens")
    if input_tokens is None or output_tokens is None:
        return NormalizedUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            source=MeasurementSource.PROVIDER_REPORTED,
            status=AccountingStatus.INCOMPLETE,
        )
    return NormalizedUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_read_tokens=_token(raw, "cache_read_input_tokens"),
        cache_write_tokens=_token(raw, "cache_creation_input_tokens"),
        total_tokens=input_tokens + output_tokens,
        source=MeasurementSource.PROVIDER_REPORTED,
        status=AccountingStatus.COMPLETE,
    )


def normalize_provider_usage(
    provider: str, raw: Mapping[str, Any] | None
) -> NormalizedUsage:
    if provider == "cloud-anthropic":
        return normalize_anthropic_usage(raw)
    if provider in {
        "local-openai-compatible",
        "cloud-openai-compatible",
        "fake-deterministic",
    }:
        return normalize_openai_usage(raw)
    raise UsageNormalizationError(f"unsupported provider usage mapping: {provider}")


_UNAVAILABLE = NormalizedUsage(
    source=MeasurementSource.UNAVAILABLE,
    status=AccountingStatus.INCOMPLETE,
)


def select_reply_usage(
    provider: str,
    *,
    native_usage: Mapping[str, Any] | None,
    native_usage_invalid: bool,
    compat_usage: Mapping[str, Any] | None,
) -> tuple[NormalizedUsage, tuple[str, ...]]:
    """Apply the ruled source-selection order for a provider reply.

    Valid native usage goes through the provider-specific normalizer.
    Absent native usage falls back to the labelled compatibility values
    (``source=provider_compatible``).  Malformed native usage is reported
    as ``native_usage_invalid`` — never silently replaced by fallback.
    """
    if native_usage_invalid:
        return _UNAVAILABLE, ("native_usage_invalid",)
    if native_usage is not None:
        try:
            return normalize_provider_usage(provider, native_usage), ()
        except UsageNormalizationError:
            return _UNAVAILABLE, ("native_usage_invalid",)
    if compat_usage is not None:
        try:
            normalized = normalize_openai_usage(compat_usage)
        except UsageNormalizationError:
            return _UNAVAILABLE, ("compatibility_usage_invalid",)
        return (
            normalized.model_copy(
                update={"source": MeasurementSource.PROVIDER_COMPATIBLE}
            ),
            ("compatibility_usage_fallback",),
        )
    return _UNAVAILABLE, ("provider_usage_missing",)