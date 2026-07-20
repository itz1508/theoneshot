from __future__ import annotations

import json
import os

import pytest

from audisor.config import AudisorConfigError, is_aflow_enabled, load_config, load_dotenv, set_aflow_enabled, set_provider_config


def test_dotenv_loads_values_without_overriding_explicit_environment(tmp_path, monkeypatch) -> None:
    path = tmp_path / ".env"
    path.write_text('FROM_DOTENV="loaded"\nEXPLICIT=from-file\n# ignored\n', encoding="utf-8")
    monkeypatch.setenv("EXPLICIT", "from-process")

    assert load_dotenv(path) == path
    assert os.environ["FROM_DOTENV"] == "loaded"
    assert os.environ["EXPLICIT"] == "from-process"


def test_dotenv_maps_existing_fireworks_names_without_overriding_explicit_values(tmp_path, monkeypatch) -> None:
    path = tmp_path / ".env"
    path.write_text(
        "FIREWORK_API_KEY=legacy-key\n",
        encoding="utf-8",
    )
    for name in (
        "AUDISOR_PROVIDER",
        "FIREWORKS_API_KEY",
        "FIREWORKS_BASE_URL",
        "FIREWORKS_MODEL",
        "FIREWORK_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)

    load_dotenv(path)

    assert os.environ["FIREWORKS_API_KEY"] == "legacy-key"


def test_config_defaults_on_and_survives_new_load(tmp_path) -> None:
    path = tmp_path / "audisor.json"
    assert is_aflow_enabled(path)
    set_aflow_enabled(False, path)
    assert load_config(path) == {"aflow_enabled": False}
    assert not is_aflow_enabled(path)
    set_aflow_enabled(True, path)
    assert is_aflow_enabled(path)


def test_saved_local_provider_configuration_survives_toggle_changes(tmp_path) -> None:
    path = tmp_path / "audisor.json"
    set_provider_config("local-openai-compatible", "http://127.0.0.1:11434", "qwen2.5-coder:7b", path)
    set_aflow_enabled(False, path)
    assert load_config(path) == {
        "aflow_enabled": False,
        "provider": "local-openai-compatible",
        "base_url": "http://127.0.0.1:11434",
        "model_id": "qwen2.5-coder:7b",
    }


def test_incomplete_saved_provider_configuration_fails_with_source_and_field(tmp_path) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"aflow_enabled": True, "provider": "local-openai-compatible"}), encoding="utf-8")
    with pytest.raises(AudisorConfigError, match="persisted_setup.*missing=base_url,model_id"):
        load_config(path)
