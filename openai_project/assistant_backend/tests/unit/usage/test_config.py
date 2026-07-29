from decimal import Decimal

import pytest

from audisor_assistant.usage import AdmissionMode, UsageConfig, UsageConfigurationError


def test_defaults_are_observe_first() -> None:
    config = UsageConfig.from_environment({})

    assert config.usage_mode is AdmissionMode.OBSERVE
    assert config.context_mode is AdmissionMode.OBSERVE
    assert config.cost_mode is AdmissionMode.OFF
    assert config.allow_remote_token_count is False
    assert config.allow_approximate_counts is True
    assert config.default_max_provider_charge_usd is None


def test_explicit_configuration_is_parsed_without_currency_float() -> None:
    config = UsageConfig.from_environment(
        {
            "AUDISOR_USAGE_MODE": "enforce",
            "AUDISOR_CONTEXT_MODE": "warn",
            "AUDISOR_COST_MODE": "observe",
            "AUDISOR_ALLOW_REMOTE_TOKEN_COUNT": "false",
            "AUDISOR_ALLOW_APPROXIMATE_COUNTS": "true",
            "AUDISOR_REQUIRE_REVIEWED_PRICING": "yes",
            "AUDISOR_DEFAULT_MAX_PROVIDER_CHARGE_USD": "0.500000",
            "AUDISOR_USAGE_LEDGER_DIR": "records/usage",
            "AUDISOR_PRICING_REGISTRY_PATH": "pricing/registry.json",
        }
    )

    assert config.usage_mode is AdmissionMode.ENFORCE
    assert config.context_mode is AdmissionMode.WARN
    assert config.default_max_provider_charge_usd == Decimal("0.500000")
    assert str(config.ledger_dir).replace("\\", "/") == "records/usage"


@pytest.mark.parametrize(
    "values, message",
    [
        ({"AUDISOR_USAGE_MODE": "sometimes"}, "unknown mode"),
        ({"AUDISOR_ALLOW_APPROXIMATE_COUNTS": "maybe"}, "boolean"),
        ({"AUDISOR_DEFAULT_MAX_PROVIDER_CHARGE_USD": "NaN"}, "non-negative"),
        ({"AUDISOR_DEFAULT_MAX_PROVIDER_CHARGE_USD": "-1"}, "non-negative"),
    ],
)
def test_invalid_configuration_is_rejected(values: dict[str, str], message: str) -> None:
    with pytest.raises(UsageConfigurationError, match=message):
        UsageConfig.from_environment(values)
