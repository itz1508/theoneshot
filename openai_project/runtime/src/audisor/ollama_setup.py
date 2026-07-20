"""Idempotent local Ollama setup and live verification."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Any, Callable, Protocol

import requests

from .config import LOCAL_PROVIDER_ID, OLLAMA_BASE_URL, OLLAMA_MODEL_ID, set_provider_config


class OllamaSetupError(RuntimeError):
    """Raised with a user-facing setup failure category."""


class CommandRunner(Protocol):
    def run(self, args: list[str], **kwargs: Any) -> Any: ...
    def popen(self, args: list[str], **kwargs: Any) -> Any: ...


class SubprocessRunner:
    run = staticmethod(subprocess.run)
    popen = staticmethod(subprocess.Popen)


@dataclass(frozen=True)
class OllamaSetupResult:
    ollama_detected: bool
    endpoint: str
    model: str
    model_available: bool
    connection_verified: bool


def _error(message: str, cause: Exception | None = None) -> OllamaSetupError:
    error = OllamaSetupError(message)
    if cause is not None:
        error.__cause__ = cause
    return error


def _executable() -> str | None:
    return shutil.which("ollama")


def _install(runner: CommandRunner) -> str:
    if os.name == "nt" and shutil.which("winget"):
        command = ["winget", "install", "--id", "Ollama.Ollama", "-e", "--accept-source-agreements", "--accept-package-agreements"]
    elif sys.platform == "darwin" and shutil.which("brew"):
        command = ["brew", "install", "ollama"]
    else:
        raise _error("Unsupported operating system")
    try:
        runner.run(command, check=True)
    except Exception as exc:
        raise _error("Ollama installation failed", exc) from exc
    executable = _executable()
    if not executable:
        raise _error("Ollama installation failed")
    return executable


def _tags(get: Callable[..., Any]) -> list[str] | None:
    try:
        response = get(f"{OLLAMA_BASE_URL}/api/tags", timeout=3)
    except Exception:
        return None
    if getattr(response, "status_code", None) != 200:
        return None
    try:
        payload = response.json()
        return [str(item["name"]) for item in payload.get("models", [])]
    except (AttributeError, KeyError, TypeError, ValueError):
        return None


def setup_ollama(
    *,
    runner: CommandRunner | None = None,
    installer: Callable[[CommandRunner], str] | None = None,
    which: Callable[[], str | None] = _executable,
    get: Callable[..., Any] = requests.get,
    post: Callable[..., Any] = requests.post,
    sleep: Callable[[float], None] = time.sleep,
    attempts: int = 20,
) -> OllamaSetupResult:
    selected_runner = runner or SubprocessRunner()
    executable = which()
    detected = executable is not None
    if executable is None:
        executable = installer(selected_runner) if installer is not None else _install(selected_runner)

    tags = _tags(get)
    if tags is None:
        try:
            selected_runner.popen([executable, "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as exc:
            raise _error("Ollama service unavailable", exc) from exc
        for _ in range(attempts):
            tags = _tags(get)
            if tags is not None:
                break
            sleep(0.25)
    if tags is None:
        raise _error("Ollama service unavailable")

    if OLLAMA_MODEL_ID not in tags:
        try:
            selected_runner.run([executable, "pull", OLLAMA_MODEL_ID], check=True)
        except Exception as exc:
            raise _error("Model download failed", exc) from exc
        tags = _tags(get)
        if tags is None or OLLAMA_MODEL_ID not in tags:
            raise _error("Model verification failed")

    try:
        response = post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json={"model": OLLAMA_MODEL_ID, "prompt": "Reply with exactly OK.", "stream": False, "options": {"num_predict": 8}},
            timeout=30,
        )
    except Exception as exc:
        raise _error("Model verification failed", exc) from exc
    if getattr(response, "status_code", None) != 200:
        raise _error("Model verification failed")
    try:
        answer = response.json().get("response", "").strip()
    except (AttributeError, TypeError, ValueError):
        answer = ""
    if not answer:
        raise _error("Model verification failed")
    set_provider_config(LOCAL_PROVIDER_ID, OLLAMA_BASE_URL, OLLAMA_MODEL_ID)
    return OllamaSetupResult(detected, OLLAMA_BASE_URL, OLLAMA_MODEL_ID, True, True)
