"""Tests for the host-agnostic configuration system.

Proves:
- Configuration precedence (operation > env > profile > file > defaults)
- Safety limit merging (most-restrictive-wins)
- Host profile selection and validation
- A-Flow separation (A-Flow ON/OFF does not affect Audisor core availability)
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from audisor.config.host_profiles import (
    AudisorConfig,
    HostProfile,
    BUILTIN_PROFILES,
    DEFAULT_MAX_TOKENS,
    DEFAULT_PROVIDER,
    DEFAULT_MODEL_ID,
    DEFAULT_BASE_URL,
    DEFAULT_TIMEOUT_SECONDS,
    load_audisor_config,
    _merge_safety_limits,
)
from audisor.schemas.authority import PermissionSet
from audisor.schemas.errors import AudisorRuntimeError


class TestConfigurationPrecedence:
    """Prove configuration precedence is respected."""

    def test_defaults_are_used_when_nothing_else(self, tmp_path: Path) -> None:
        config = load_audisor_config(config_file_path=tmp_path / "nonexistent.json")
        assert config.provider == DEFAULT_PROVIDER
        assert config.model_id == DEFAULT_MODEL_ID
        assert config.base_url == DEFAULT_BASE_URL
        assert config.timeout_seconds == DEFAULT_TIMEOUT_SECONDS
        assert config.max_tokens == DEFAULT_MAX_TOKENS
        assert config.selected_profile == "cli"

    def test_config_file_overrides_defaults(self, tmp_path: Path) -> None:
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({
            "provider": "custom-provider",
            "model_id": "custom-model",
            "selected_profile": "codex",
        }))
        config = load_audisor_config(config_file_path=config_file)
        assert config.provider == "custom-provider"
        assert config.model_id == "custom-model"
        assert config.selected_profile == "codex"
        # Defaults still apply for unspecified keys
        assert config.base_url == DEFAULT_BASE_URL

    def test_environment_overrides_config_file(self, tmp_path: Path, monkeypatch) -> None:
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({
            "provider": "file-provider",
            "model_id": "file-model",
        }))
        monkeypatch.setenv("AUDISOR_PROVIDER", "env-provider")
        monkeypatch.setenv("AUDISOR_MODEL_ID", "env-model")
        config = load_audisor_config(config_file_path=config_file)
        assert config.provider == "env-provider"
        assert config.model_id == "env-model"

    def test_operation_overrides_environment(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("AUDISOR_PROVIDER", "env-provider")
        config = load_audisor_config(
            config_file_path=tmp_path / "nonexistent.json",
            operation_overrides={"provider": "op-provider"},
        )
        assert config.provider == "op-provider"

    def test_profile_defaults_apply_when_no_override(self, tmp_path: Path) -> None:
        config = load_audisor_config(
            config_file_path=tmp_path / "nonexistent.json",
            operation_overrides={"selected_profile": "codex"},
        )
        profile = config.get_profile()
        assert profile.profile_id == "codex"
        assert profile.adapter_type == "codex"
        assert profile.aflow_enabled_by_default is True


class TestSafetyLimitMerging:
    """Prove safety limits use most-restrictive-wins."""

    def test_numeric_limits_take_minimum(self) -> None:
        base = {"max_tokens": 1000, "timeout_seconds": 300.0}
        profile = {"max_tokens": 500}
        env = {"timeout_seconds": 60.0}
        request = {"max_tokens": 2000}  # Less restrictive, should not win
        result = _merge_safety_limits(base, profile, env, request)
        assert result["max_tokens"] == 500  # profile wins (minimum)
        assert result["timeout_seconds"] == 60.0  # env wins (minimum)

    def test_boolean_limits_true_wins(self) -> None:
        base = {"aflow_enabled": False}
        profile = {"aflow_enabled": True}
        env = {}
        request = {}
        result = _merge_safety_limits(base, profile, env, request)
        assert result["aflow_enabled"] is True  # True is more restrictive

    def test_list_limits_union(self) -> None:
        base = {"prohibited_paths": [".git"]}
        profile = {"prohibited_paths": [".codex"]}
        env = {}
        request = {}
        result = _merge_safety_limits(base, profile, env, request)
        assert ".git" in result["prohibited_paths"]
        assert ".codex" in result["prohibited_paths"]

    def test_request_can_restrict_further(self) -> None:
        base = {"max_tokens": 1000}
        profile = {}
        env = {}
        request = {"max_tokens": 100}  # More restrictive
        result = _merge_safety_limits(base, profile, env, request)
        assert result["max_tokens"] == 100


class TestHostProfiles:
    """Prove host profiles are correctly defined."""

    def test_all_builtin_profiles_exist(self) -> None:
        expected = {"codex", "generic_mcp", "responses_compatible", "cli"}
        assert set(BUILTIN_PROFILES.keys()) == expected

    def test_codex_profile_has_correct_capabilities(self) -> None:
        profile = BUILTIN_PROFILES["codex"]
        assert profile.capabilities["build"] is True
        assert profile.capabilities["fix"] is True
        assert profile.capabilities["analyze"] is True
        assert profile.capabilities["validate"] is True
        assert profile.supports_tools is True
        assert profile.supports_artifacts is True
        assert profile.aflow_enabled_by_default is True

    def test_responses_compatible_profile_is_read_only(self) -> None:
        profile = BUILTIN_PROFILES["responses_compatible"]
        assert profile.capabilities["build"] is False
        assert profile.capabilities["fix"] is False
        assert profile.capabilities["analyze"] is True
        assert profile.capabilities["validate"] is True
        assert profile.supports_streaming is True
        assert profile.supports_tools is False
        assert profile.aflow_enabled_by_default is False

    def test_generic_mcp_profile_has_no_artifacts(self) -> None:
        profile = BUILTIN_PROFILES["generic_mcp"]
        assert profile.supports_artifacts is False
        assert profile.aflow_enabled_by_default is False

    def test_cli_profile_is_permissive(self) -> None:
        profile = BUILTIN_PROFILES["cli"]
        assert profile.default_permissions.prohibited_paths == []
        assert profile.default_permissions.prohibited_tools == []
        assert profile.aflow_enabled_by_default is True

    def test_unknown_profile_raises_error(self, tmp_path: Path) -> None:
        with pytest.raises(AudisorRuntimeError) as exc_info:
            load_audisor_config(
                config_file_path=tmp_path / "nonexistent.json",
                operation_overrides={"selected_profile": "nonexistent"},
            )
        assert "unknown_host_profile" in str(exc_info.value)


class TestAFlowSeparation:
    """Prove A-Flow ON/OFF does not affect Audisor core availability."""

    def test_aflow_enabled_does_not_block_audisor_core(self, tmp_path: Path) -> None:
        """Audisor core must remain callable regardless of A-Flow toggle."""
        config_on = load_audisor_config(
            config_file_path=tmp_path / "nonexistent.json",
            operation_overrides={"aflow_enabled": True},
        )
        config_off = load_audisor_config(
            config_file_path=tmp_path / "nonexistent.json",
            operation_overrides={"aflow_enabled": False},
        )
        # Both configs should be valid and have the same provider settings
        assert config_on.provider == config_off.provider
        assert config_on.model_id == config_off.model_id
        # A-Flow toggle should differ
        assert config_on.aflow_enabled is True
        assert config_off.aflow_enabled is False

    def test_profile_aflow_default_is_respected(self, tmp_path: Path) -> None:
        """Profile default for aflow_enabled is used when not overridden."""
        # responses_compatible defaults to False
        config = load_audisor_config(
            config_file_path=tmp_path / "nonexistent.json",
            operation_overrides={"selected_profile": "responses_compatible"},
        )
        assert config.aflow_enabled is False

        # codex defaults to True
        config = load_audisor_config(
            config_file_path=tmp_path / "nonexistent.json",
            operation_overrides={"selected_profile": "codex"},
        )
        assert config.aflow_enabled is True

    def test_explicit_override_trumps_profile_default(self, tmp_path: Path) -> None:
        """Operation override can force aflow_enabled even if profile defaults differ."""
        config = load_audisor_config(
            config_file_path=tmp_path / "nonexistent.json",
            operation_overrides={
                "selected_profile": "responses_compatible",
                "aflow_enabled": True,
            },
        )
        assert config.aflow_enabled is True


class TestConfigSerialization:
    """Prove config can be serialized for inspection."""

    def test_to_mapping_contains_expected_keys(self, tmp_path: Path) -> None:
        config = load_audisor_config(config_file_path=tmp_path / "nonexistent.json")
        mapping = config.to_mapping()
        assert "aflow_enabled" in mapping
        assert "provider" in mapping
        assert "model_id" in mapping
        assert "base_url" in mapping
        assert "timeout_seconds" in mapping
        assert "max_tokens" in mapping
        assert "selected_profile" in mapping
        assert "profile_ids" in mapping
        assert "safety_limits" in mapping


class TestEnvironmentOverrides:
    """Prove environment variable overrides work correctly."""

    def test_aflow_enabled_env_var(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("AUDISOR_AFLOW_ENABLED", "false")
        config = load_audisor_config(config_file_path=tmp_path / "nonexistent.json")
        assert config.aflow_enabled is False

    def test_numeric_env_vars(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("AUDISOR_TIMEOUT_SECONDS", "120")
        monkeypatch.setenv("AUDISOR_MAX_TOKENS", "320")
        config = load_audisor_config(config_file_path=tmp_path / "nonexistent.json")
        assert config.timeout_seconds == 120.0
        assert config.max_tokens == 320

    def test_invalid_env_var_raises_error(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("AUDISOR_TIMEOUT_SECONDS", "not_a_number")
        with pytest.raises(AudisorRuntimeError) as exc_info:
            load_audisor_config(config_file_path=tmp_path / "nonexistent.json")
        assert "environment_override_invalid" in str(exc_info.value)