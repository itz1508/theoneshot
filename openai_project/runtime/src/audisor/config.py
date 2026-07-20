"""User-level Audisor configuration for the local CLI lifecycle."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

OLLAMA_BASE_URL = "http://127.0.0.1:11434"
OLLAMA_MODEL_ID = "qwen2.5-coder:7b"
AUDISOR_CONFIG_ENV = "AUDISOR_CONFIG_PATH"
AUDISOR_ENV_FILE = "AUDISOR_ENV_FILE"
LOCAL_PROVIDER_ID = "local-openai-compatible"


class AudisorConfigError(RuntimeError):
    """Raised when the persisted local configuration is malformed."""


def load_dotenv(path: Path | None = None) -> Path | None:
    """Load simple KEY=VALUE entries without overriding explicit env vars."""
    selected = path
    if selected is None:
        override = os.environ.get(AUDISOR_ENV_FILE, "").strip()
        if override:
            selected = Path(override).expanduser()
        else:
            runtime_env = Path(__file__).resolve().parents[2] / ".env"
            selected = runtime_env if runtime_env.exists() else Path.cwd() / ".env"
    if not selected.exists():
        return None
    for raw_line in selected.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, separator, value = line.partition("=")
        if not separator or not key.strip():
            continue
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        os.environ.setdefault(key, value)
    _apply_fireworks_aliases()
    return selected


def _apply_fireworks_aliases() -> None:
    """Support the existing local Fireworks names without replacing explicit settings."""
    aliases = {
        "FIREWORK_API_KEY": "FIREWORKS_API_KEY",
    }
    for source, target in aliases.items():
        if not os.environ.get(target, "").strip() and os.environ.get(source, "").strip():
            os.environ[target] = os.environ[source]


def config_path() -> Path:
    override = os.environ.get(AUDISOR_CONFIG_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "Audisor" / "config.json"
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "audisor" / "config.json"


def load_config(path: Path | None = None) -> dict[str, Any]:
    selected = path or config_path()
    if not selected.exists():
        return {"aflow_enabled": True}
    try:
        value: Any = json.loads(selected.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AudisorConfigError("Audisor configuration could not be read") from exc
    if not isinstance(value, dict) or not isinstance(value.get("aflow_enabled"), bool):
        raise AudisorConfigError("Audisor configuration has an invalid aflow_enabled value")
    provider_fields = ("provider", "base_url", "model_id")
    present = [field for field in provider_fields if field in value]
    if present and set(present) != set(provider_fields):
        missing = sorted(set(provider_fields) - set(present))
        raise AudisorConfigError(
            "Audisor persisted_setup configuration is incomplete: "
            f"missing={','.join(missing)}; expected provider, base_url, and model_id"
        )
    if present:
        if value["provider"] != LOCAL_PROVIDER_ID:
            raise AudisorConfigError(
                "Audisor persisted_setup configuration has an unsupported provider: "
                f"expected={LOCAL_PROVIDER_ID}"
            )
        if not all(isinstance(value[field], str) and value[field].strip() for field in provider_fields):
            raise AudisorConfigError(
                "Audisor persisted_setup configuration has invalid provider fields: "
                "expected non-empty provider, base_url, and model_id"
            )
        return {"aflow_enabled": value["aflow_enabled"], **{field: value[field] for field in provider_fields}}
    return {"aflow_enabled": value["aflow_enabled"]}


def is_aflow_enabled(path: Path | None = None) -> bool:
    return load_config(path)["aflow_enabled"]


def set_aflow_enabled(enabled: bool, path: Path | None = None) -> Path:
    selected = path or config_path()
    selected.parent.mkdir(parents=True, exist_ok=True)
    current = load_config(selected) if selected.exists() else {"aflow_enabled": True}
    current["aflow_enabled"] = bool(enabled)
    temporary = selected.with_suffix(selected.suffix + ".tmp")
    temporary.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    os.replace(temporary, selected)
    return selected


def set_provider_config(
    provider: str,
    base_url: str,
    model_id: str,
    path: Path | None = None,
) -> Path:
    """Persist the verified local provider without replacing the A-Flow toggle."""
    if provider != LOCAL_PROVIDER_ID or not all(isinstance(value, str) and value.strip() for value in (provider, base_url, model_id)):
        raise AudisorConfigError(
            "Audisor persisted_setup configuration is invalid: expected "
            "provider=local-openai-compatible with non-empty base_url and model_id"
        )
    selected = path or config_path()
    current = load_config(selected) if selected.exists() else {"aflow_enabled": True}
    current.update({"provider": provider, "base_url": base_url, "model_id": model_id})
    # Revalidate the complete record before publishing it.
    validated = dict(current)
    temporary = selected.with_suffix(selected.suffix + ".tmp")
    selected.parent.mkdir(parents=True, exist_ok=True)
    temporary.write_text(json.dumps(validated, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    os.replace(temporary, selected)
    return selected


def load_provider_config(path: Path | None = None) -> dict[str, str] | None:
    value = load_config(path)
    if "provider" not in value:
        return None
    return {field: value[field] for field in ("provider", "base_url", "model_id")}
