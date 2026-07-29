"""Environment parsing for usage accounting and admission modes."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .policies import AdmissionMode


class UsageConfigurationError(ValueError):
    pass


def _mode(values: Mapping[str, str], name: str, default: AdmissionMode) -> AdmissionMode:
    raw = values.get(name, default.value).strip()
    try:
        return AdmissionMode(raw)
    except ValueError as error:
        raise UsageConfigurationError(f"{name} has an unknown mode") from error


def _boolean(values: Mapping[str, str], name: str, default: bool) -> bool:
    raw = values.get(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise UsageConfigurationError(f"{name} must be a boolean")


def _decimal(values: Mapping[str, str], name: str) -> Decimal | None:
    raw = values.get(name, "").strip()
    if not raw:
        return None
    try:
        value = Decimal(raw)
    except InvalidOperation as error:
        raise UsageConfigurationError(f"{name} must be a decimal") from error
    if not value.is_finite() or value < 0:
        raise UsageConfigurationError(f"{name} must be a non-negative finite decimal")
    return value


def _path(values: Mapping[str, str], name: str) -> Path | None:
    raw = values.get(name, "").strip()
    return Path(raw).expanduser() if raw else None


@dataclass(frozen=True)
class UsageConfig:
    usage_mode: AdmissionMode
    context_mode: AdmissionMode
    cost_mode: AdmissionMode
    allow_remote_token_count: bool
    allow_approximate_counts: bool
    require_reviewed_pricing: bool
    default_max_provider_charge_usd: Decimal | None
    ledger_dir: Path | None
    pricing_registry_path: Path | None

    @classmethod
    def from_environment(
        cls, values: Mapping[str, str] | None = None
    ) -> "UsageConfig":
        source = os.environ if values is None else values
        return cls(
            usage_mode=_mode(source, "AUDISOR_USAGE_MODE", AdmissionMode.OBSERVE),
            context_mode=_mode(
                source, "AUDISOR_CONTEXT_MODE", AdmissionMode.OBSERVE
            ),
            cost_mode=_mode(source, "AUDISOR_COST_MODE", AdmissionMode.OFF),
            allow_remote_token_count=_boolean(
                source, "AUDISOR_ALLOW_REMOTE_TOKEN_COUNT", False
            ),
            allow_approximate_counts=_boolean(
                source, "AUDISOR_ALLOW_APPROXIMATE_COUNTS", True
            ),
            require_reviewed_pricing=_boolean(
                source, "AUDISOR_REQUIRE_REVIEWED_PRICING", False
            ),
            default_max_provider_charge_usd=_decimal(
                source, "AUDISOR_DEFAULT_MAX_PROVIDER_CHARGE_USD"
            ),
            ledger_dir=_path(source, "AUDISOR_USAGE_LEDGER_DIR"),
            pricing_registry_path=_path(source, "AUDISOR_PRICING_REGISTRY_PATH"),
        )
