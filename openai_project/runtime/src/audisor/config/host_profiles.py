"""Host-agnostic configuration system for Audisor.

Configuration precedence (most to least authoritative):
1. Operation request overrides (per-request, ephemeral)
2. Environment variable overrides (AUDISOR_*)
3. Selected host profile (codex, generic-mcp, responses-compatible, cli)
4. Audisor config file (~/.config/audisor/config.json)
5. Built-in defaults

Safety limits use most-restrictive-wins merge.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Mapping

from audisor.schemas.authority import AuthorityContext, PermissionSet
from audisor.schemas.errors import AudisorRuntimeError


# ── Built-in defaults ──────────────────────────────────────────────────────────

DEFAULT_PROVIDER = "local-openai-compatible"
DEFAULT_MODEL_ID = "qwen2.5-coder:7b"
DEFAULT_BASE_URL = "http://127.0.0.1:11434"
DEFAULT_TIMEOUT_SECONDS = 300.0
DEFAULT_MAX_TOKENS = 160


# ── Host profile definitions ───────────────────────────────────────────────────

@dataclass(frozen=True)
class HostProfile:
    """Immutable host profile defining adapter behavior and constraints."""

    profile_id: str
    adapter_type: Literal["codex", "generic_mcp", "responses_compatible", "cli"]
    request_adapter: str  # import path to HostRequestAdapter implementation
    response_adapter: str  # import path to HostResponseAdapter implementation
    capabilities: dict[str, Any] = field(default_factory=dict)
    default_permissions: PermissionSet = field(default_factory=PermissionSet)
    max_request_size_bytes: int = 1_000_000
    max_response_size_bytes: int = 1_000_000
    supports_streaming: bool = False
    supports_tools: bool = False
    supports_artifacts: bool = False
    aflow_enabled_by_default: bool = True

    def to_mapping(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "adapter_type": self.adapter_type,
            "request_adapter": self.request_adapter,
            "response_adapter": self.response_adapter,
            "capabilities": dict(self.capabilities),
            "default_permissions": self.default_permissions.model_dump(mode="json"),
            "max_request_size_bytes": self.max_request_size_bytes,
            "max_response_size_bytes": self.max_response_size_bytes,
            "supports_streaming": self.supports_streaming,
            "supports_tools": self.supports_tools,
            "supports_artifacts": self.supports_artifacts,
            "aflow_enabled_by_default": self.aflow_enabled_by_default,
        }


BUILTIN_PROFILES: dict[str, HostProfile] = {
    "codex": HostProfile(
        profile_id="codex",
        adapter_type="codex",
        request_adapter="audisor.adapters.codex.CodexRequestAdapter",
        response_adapter="audisor.adapters.codex.CodexResponseAdapter",
        capabilities={"build": True, "fix": True, "analyze": True, "validate": True},
        default_permissions=PermissionSet(
            allowed_paths=["."],
            prohibited_paths=[".git", ".codex", "audisor-state"],
            allowed_tools=["read_file", "write_file", "replace_in_file", "execute_command"],
            prohibited_tools=["delete_file", "move_file"],
        ),
        supports_streaming=False,
        supports_tools=True,
        supports_artifacts=True,
        aflow_enabled_by_default=True,
    ),
    "generic_mcp": HostProfile(
        profile_id="generic_mcp",
        adapter_type="generic_mcp",
        request_adapter="audisor.adapters.mcp.MCPRequestAdapter",
        response_adapter="audisor.adapters.mcp.MCPResponseAdapter",
        capabilities={"build": True, "fix": True, "analyze": True, "validate": True},
        default_permissions=PermissionSet(
            allowed_paths=["."],
            prohibited_paths=[".git", ".codex"],
            allowed_tools=[],
            prohibited_tools=[],
        ),
        supports_streaming=False,
        supports_tools=True,
        supports_artifacts=False,
        aflow_enabled_by_default=False,
    ),
    "responses_compatible": HostProfile(
        profile_id="responses_compatible",
        adapter_type="responses_compatible",
        request_adapter="audisor.adapters.responses.ResponsesRequestAdapter",
        response_adapter="audisor.adapters.responses.ResponsesResponseAdapter",
        capabilities={"build": False, "fix": False, "analyze": True, "validate": True},
        default_permissions=PermissionSet(
            allowed_paths=["."],
            prohibited_paths=[".git", ".codex", "audisor-state"],
            allowed_tools=["read_file"],
            prohibited_tools=["write_file", "replace_in_file", "execute_command", "delete_file"],
        ),
        max_request_size_bytes=500_000,
        max_response_size_bytes=500_000,
        supports_streaming=True,
        supports_tools=False,
        supports_artifacts=False,
        aflow_enabled_by_default=False,
    ),
    "cli": HostProfile(
        profile_id="cli",
        adapter_type="cli",
        request_adapter="audisor.adapters.cli.CLIRequestAdapter",
        response_adapter="audisor.adapters.cli.CLIResponseAdapter",
        capabilities={"build": True, "fix": True, "analyze": True, "validate": True},
        default_permissions=PermissionSet(
            allowed_paths=["."],
            prohibited_paths=[],
            allowed_tools=[],
            prohibited_tools=[],
        ),
        supports_streaming=False,
        supports_tools=False,
        supports_artifacts=False,
        aflow_enabled_by_default=True,
    ),
}


# ── Configuration schema ───────────────────────────────────────────────────────

@dataclass(frozen=True)
class AudisorConfig:
    """Resolved Audisor configuration with all precedence layers applied."""

    aflow_enabled: bool
    provider: str
    model_id: str
    base_url: str
    timeout_seconds: float
    max_tokens: int
    selected_profile: str
    profiles: dict[str, HostProfile]
    environment_overrides: dict[str, Any]
    config_file_path: Path | None
    safety_limits: dict[str, Any] = field(default_factory=dict)

    def get_profile(self) -> HostProfile:
        """Return the currently selected host profile."""
        if self.selected_profile not in self.profiles:
            raise AudisorRuntimeError(
                category="configuration",
                stage="request_translation",
                code="unknown_host_profile",
                message=f"Unknown host profile: {self.selected_profile}",
            )
        return self.profiles[self.selected_profile]

    def to_mapping(self) -> dict[str, Any]:
        return {
            "aflow_enabled": self.aflow_enabled,
            "provider": self.provider,
            "model_id": self.model_id,
            "base_url": self.base_url,
            "timeout_seconds": self.timeout_seconds,
            "max_tokens": self.max_tokens,
            "selected_profile": self.selected_profile,
            "profile_ids": list(self.profiles.keys()),
            "environment_overrides": dict(self.environment_overrides),
            "config_file_path": str(self.config_file_path) if self.config_file_path else None,
            "safety_limits": dict(self.safety_limits),
        }


# ── Configuration loader ───────────────────────────────────────────────────────

def _config_path() -> Path:
    """Return the Audisor configuration file path."""
    override = os.environ.get("AUDISOR_CONFIG_PATH", "").strip()
    if override:
        return Path(override).expanduser()
    if os.name == "nt" or os.environ.get("OS", "").lower().startswith("windows"):
        local_app_data = os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")
        return Path(local_app_data) / "Audisor" / "config.json"
    config_home = os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")
    return Path(config_home) / "audisor" / "config.json"


def _load_config_file(path: Path | None = None) -> dict[str, Any]:
    """Load the Audisor configuration file if it exists."""
    selected = path or _config_path()
    if not selected.exists():
        return {}
    try:
        with selected.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        raise AudisorRuntimeError(
            category="configuration",
            stage="request_translation",
            code="config_file_unreadable",
            message=f"Cannot read config file: {exc}",
        ) from exc
    if not isinstance(data, dict):
        raise AudisorRuntimeError(
            category="configuration",
            stage="request_translation",
            code="config_file_malformed",
            message="Config file must contain a JSON object",
        )
    return data


def _load_environment_overrides() -> dict[str, Any]:
    """Load environment variable overrides."""
    overrides: dict[str, Any] = {}
    env_map = {
        "AUDISOR_AFLOW_ENABLED": ("aflow_enabled", lambda v: v.lower() in ("1", "true", "yes", "on")),
        "AUDISOR_PROVIDER": ("provider", str),
        "AUDISOR_MODEL_ID": ("model_id", str),
        "AUDISOR_BASE_URL": ("base_url", str),
        "AUDISOR_TIMEOUT_SECONDS": ("timeout_seconds", float),
        "AUDISOR_MAX_TOKENS": ("max_tokens", int),
        "AUDISOR_SELECTED_PROFILE": ("selected_profile", str),
    }
    for env_var, (config_key, converter) in env_map.items():
        value = os.environ.get(env_var, "").strip()
        if value:
            try:
                overrides[config_key] = converter(value)
            except (ValueError, TypeError) as exc:
                raise AudisorRuntimeError(
                    category="configuration",
                    stage="request_translation",
                    code="environment_override_invalid",
                    message=f"Invalid value for {env_var}: {exc}",
                ) from exc
    return overrides


def _merge_safety_limits(
    base: dict[str, Any],
    profile: dict[str, Any],
    environment: dict[str, Any],
    request: dict[str, Any],
) -> dict[str, Any]:
    """Merge safety limits using most-restrictive-wins.

    For numeric limits (max_tokens, timeout_seconds), the minimum wins.
    For boolean limits (aflow_enabled), True wins (more restrictive).
    For list limits (prohibited_paths, prohibited_tools), union wins.
    """
    result = dict(base)

    def apply_layer(layer: dict[str, Any]) -> None:
        for key, value in layer.items():
            if key not in result:
                result[key] = value
                continue
            existing = result[key]
            if isinstance(existing, bool) and isinstance(value, bool):
                # True is more restrictive for safety limits
                result[key] = existing or value
            elif isinstance(existing, (int, float)) and isinstance(value, (int, float)):
                # Lower is more restrictive
                result[key] = min(existing, value)
            elif isinstance(existing, list) and isinstance(value, list):
                # Union is more restrictive for prohibitions
                result[key] = list(dict.fromkeys(existing + value))
            else:
                result[key] = value

    apply_layer(profile)
    apply_layer(environment)
    apply_layer(request)
    return result


def load_audisor_config(
    *,
    config_file_path: Path | None = None,
    operation_overrides: Mapping[str, Any] | None = None,
    custom_profiles: Mapping[str, HostProfile] | None = None,
) -> AudisorConfig:
    """Load Audisor configuration with full precedence resolution.

    Precedence (most to least authoritative):
    1. Operation request overrides
    2. Environment variable overrides
    3. Selected host profile defaults
    4. Config file values
    5. Built-in defaults
    """
    # Layer 5: Built-in defaults
    defaults = {
        "aflow_enabled": True,
        "provider": DEFAULT_PROVIDER,
        "model_id": DEFAULT_MODEL_ID,
        "base_url": DEFAULT_BASE_URL,
        "timeout_seconds": DEFAULT_TIMEOUT_SECONDS,
        "max_tokens": DEFAULT_MAX_TOKENS,
        "selected_profile": "cli",
    }

    # Layer 4: Config file
    file_data = _load_config_file(config_file_path)
    file_path = config_file_path or _config_path()

    # Layer 3: Host profiles
    profiles = dict(BUILTIN_PROFILES)
    if custom_profiles:
        profiles.update(custom_profiles)

    # Determine selected profile
    selected_profile = (
        operation_overrides.get("selected_profile")
        if operation_overrides
        else None
    ) or file_data.get("selected_profile", defaults["selected_profile"])

    if selected_profile not in profiles:
        raise AudisorRuntimeError(
            category="configuration",
            stage="request_translation",
            code="unknown_host_profile",
            message=f"Unknown host profile: {selected_profile}",
        )

    profile = profiles[selected_profile]

    # Layer 2: Environment overrides
    env_overrides = _load_environment_overrides()

    # Layer 1: Operation request overrides
    op_overrides = dict(operation_overrides) if operation_overrides else {}

    # Resolve each config key with precedence
    def resolve(key: str) -> Any:
        if key in op_overrides:
            return op_overrides[key]
        if key in env_overrides:
            return env_overrides[key]
        if key == "aflow_enabled" and profile.aflow_enabled_by_default is not None:
            # Profile default for aflow_enabled
            pass  # Will fall through to file_data or defaults
        if key in file_data:
            return file_data[key]
        return defaults[key]

    # Special handling: aflow_enabled uses profile default if not overridden
    aflow_enabled = op_overrides.get("aflow_enabled")
    if aflow_enabled is None:
        aflow_enabled = env_overrides.get("aflow_enabled")
    if aflow_enabled is None:
        aflow_enabled = file_data.get("aflow_enabled")
    if aflow_enabled is None:
        aflow_enabled = profile.aflow_enabled_by_default

    # Merge safety limits
    base_limits = {
        "max_tokens": DEFAULT_MAX_TOKENS,
        "timeout_seconds": DEFAULT_TIMEOUT_SECONDS,
    }
    profile_limits = {
        "max_tokens": profile.max_request_size_bytes // 1000,  # Rough approximation
    }
    safety_limits = _merge_safety_limits(
        base_limits,
        profile_limits,
        {k: v for k, v in env_overrides.items() if k in ("max_tokens", "timeout_seconds")},
        {k: v for k, v in op_overrides.items() if k in ("max_tokens", "timeout_seconds")},
    )

    return AudisorConfig(
        aflow_enabled=bool(aflow_enabled),
        provider=str(resolve("provider")),
        model_id=str(resolve("model_id")),
        base_url=str(resolve("base_url")),
        timeout_seconds=float(resolve("timeout_seconds")),
        max_tokens=int(resolve("max_tokens")),
        selected_profile=selected_profile,
        profiles=profiles,
        environment_overrides=env_overrides,
        config_file_path=file_path if file_path.exists() else None,
        safety_limits=safety_limits,
    )