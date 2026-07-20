from __future__ import annotations

import json

import pytest

from audisor.ollama_setup import OLLAMA_MODEL_ID, OllamaSetupError, setup_ollama


class Response:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


class Runner:
    def __init__(self):
        self.commands = []

    def run(self, args, **kwargs):
        self.commands.append(args)

    def popen(self, args, **kwargs):
        self.commands.append(args)


def test_existing_ollama_and_model_are_reused_without_pull(tmp_path, monkeypatch):
    runner = Runner()
    monkeypatch.setenv("AUDISOR_CONFIG_PATH", str(tmp_path / "config.json"))
    result = setup_ollama(runner=runner, which=lambda: "ollama", get=lambda *a, **k: Response(payload={"models": [{"name": OLLAMA_MODEL_ID}, {"name": "other"}]}), post=lambda *a, **k: Response(payload={"response": "OK"}))
    assert result.model_available and runner.commands == []
    assert json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))["provider"] == "local-openai-compatible"


def test_missing_ollama_invokes_injected_install_path():
    runner = Runner()
    result = setup_ollama(
        runner=runner,
        installer=lambda selected: (selected.commands.append(["installer"]), "ollama")[1],
        which=lambda: None,
        get=lambda *a, **k: Response(payload={"models": [{"name": OLLAMA_MODEL_ID}]}),
        post=lambda *a, **k: Response(payload={"response": "OK"}),
    )
    assert result.ollama_detected is False
    assert runner.commands == [["installer"]]


def test_missing_model_is_pulled_and_other_models_are_preserved():
    runner = Runner()
    calls = [0]

    def get(*args, **kwargs):
        calls[0] += 1
        names = ["other"] if calls[0] == 1 else [OLLAMA_MODEL_ID, "other"]
        return Response(payload={"models": [{"name": name} for name in names]})

    setup_ollama(runner=runner, which=lambda: "ollama", get=get, post=lambda *a, **k: Response(payload={"response": "OK"}))
    assert ["ollama", "pull", OLLAMA_MODEL_ID] in runner.commands
    assert ["ollama", "rm", "other"] not in runner.commands


def test_unavailable_service_and_failed_model_verification_are_reported():
    with pytest.raises(OllamaSetupError, match="Ollama service unavailable"):
        setup_ollama(runner=Runner(), which=lambda: "ollama", get=lambda *a, **k: Response(503), post=lambda *a, **k: Response(503), attempts=0)
    with pytest.raises(OllamaSetupError, match="Model verification failed"):
        setup_ollama(runner=Runner(), which=lambda: "ollama", get=lambda *a, **k: Response(payload={"models": [{"name": OLLAMA_MODEL_ID}]}), post=lambda *a, **k: Response(500))
