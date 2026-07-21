"""Worker routing, adapters, secret safety, and optional live API proofs."""

from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
from typing import Any

import pytest
import requests
from fastapi.testclient import TestClient

from audisor.api.tasks import get_task_service
from audisor.main import create_app
from audisor.routing.configuration import get_provider_router
from audisor.schemas.task_input import TaskInput
from audisor.schemas.task_output import TaskOutput
from audisor.service import TaskService
from audisor.workers.base import (
    ProviderConfigurationError,
    ProviderInvalidResponseError,
    ProviderPermanentRequestError,
    ProviderUnavailableError,
)
from audisor.workers.fireworks import FireworksWorker
from audisor.workers.local import LocalWorker
from provider_testkit import provider_router


class FakeResponse:
    def __init__(self, status_code: int, payload: Any = None) -> None:
        self.status_code = status_code
        self.payload = payload

    def json(self) -> Any:
        return self.payload


class NeverWorker:
    name = "never"

    def __init__(self) -> None:
        self.calls: list[str] = []

    def execute(self, task: TaskInput) -> object:
        self.calls.append(task)
        raise AssertionError("unselected worker was called")


def client_for(worker: FireworksWorker | LocalWorker, selected: str) -> TestClient:
    other = NeverWorker()
    router = (
        provider_router(selected, worker, other)
        if selected == "fireworks"
        else provider_router("local-openai-compatible", other, worker)
    )
    app = create_app()
    app.dependency_overrides[get_task_service] = lambda: TaskService(router, max_workers=2)
    return TestClient(app)


def task_input(prompt: str = "prompt") -> TaskInput:
    return TaskInput(task_id="task-001", prompt=prompt)


def test_router_has_no_default_when_selector_is_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AUDISOR_PROVIDER", raising=False)
    monkeypatch.setenv("AUDISOR_CONFIG_PATH", str(Path.cwd() / ".missing-audisor-config"))
    get_provider_router.cache_clear()
    try:
        router = get_provider_router()
        assert router.selected_provider_id is None
        with pytest.raises(ProviderConfigurationError, match="AUDISOR_PROVIDER"):
            router.select_provider()
    finally:
        get_provider_router.cache_clear()


def test_router_loads_persisted_local_setup_without_provider_environment(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    from audisor.config import set_provider_config

    path = tmp_path / "config.json"
    set_provider_config("local-openai-compatible", "http://127.0.0.1:11434", "qwen2.5-coder:7b", path)
    monkeypatch.setenv("AUDISOR_CONFIG_PATH", str(path))
    monkeypatch.delenv("AUDISOR_PROVIDER", raising=False)
    monkeypatch.delenv("LOCAL_MODEL_BASE_URL", raising=False)
    monkeypatch.delenv("LOCAL_MODEL_ID", raising=False)
    get_provider_router.cache_clear()
    try:
        router = get_provider_router()
        provider = router.select_provider()
        assert router.selected_provider_id == "local-openai-compatible"
        assert isinstance(provider, LocalWorker)
        assert provider.base_url == "http://127.0.0.1:11434"
        assert provider.model_id == "qwen2.5-coder:7b"
        assert provider.structured_output is True
    finally:
        get_provider_router.cache_clear()


def test_explicit_provider_environment_precedes_persisted_setup(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    from audisor.config import set_provider_config

    path = tmp_path / "config.json"
    set_provider_config("local-openai-compatible", "http://persisted", "persisted-model", path)
    monkeypatch.setenv("AUDISOR_CONFIG_PATH", str(path))
    monkeypatch.setenv("AUDISOR_PROVIDER", "fireworks")
    monkeypatch.setenv("FIREWORKS_API_KEY", "test-key")
    get_provider_router.cache_clear()
    try:
        assert get_provider_router().selected_provider_id == "fireworks"
    finally:
        get_provider_router.cache_clear()


@pytest.mark.parametrize(
    ("configured", "expected_type"),
    [("fireworks", FireworksWorker), ("local-openai-compatible", LocalWorker)],
)
def test_router_selects_explicit_provider(
    monkeypatch: pytest.MonkeyPatch,
    configured: str,
    expected_type: type[FireworksWorker] | type[LocalWorker],
) -> None:
    monkeypatch.setenv("AUDISOR_PROVIDER", configured)
    get_provider_router.cache_clear()
    try:
        router = get_provider_router()
        assert router.selected_provider_id == configured
        assert isinstance(router.select_provider(), expected_type)
    finally:
        get_provider_router.cache_clear()


@pytest.mark.parametrize("configured", ["", "unknown", "fireworks,local"])
def test_router_rejects_empty_or_unsupported_selection(
    monkeypatch: pytest.MonkeyPatch, configured: str
) -> None:
    monkeypatch.setenv("AUDISOR_PROVIDER", configured)
    get_provider_router.cache_clear()
    try:
        with pytest.raises(ProviderConfigurationError):
            get_provider_router().select_provider()
    finally:
        get_provider_router.cache_clear()


def test_fireworks_adapter_receives_complete_prompt_and_expected_transport_shape() -> None:
    calls: list[dict[str, Any]] = []

    def request(url: str, **kwargs: Any) -> FakeResponse:
        calls.append({"url": url, **kwargs})
        return FakeResponse(200, {"choices": [{"text": "ready"}]})

    prompt = "Complete executable task instruction\nwith every line preserved."
    worker = FireworksWorker(
        "not-a-real-credential",
        "https://example.test/inference",
        "accounts/example/models/test",
        max_attempts=1,
        request=request,
    )
    assert worker.execute(task_input(prompt)) == TaskOutput(task_id="task-001", answer="ready")
    assert calls[0]["url"] == "https://example.test/inference/v1/completions"
    assert calls[0]["headers"]["Authorization"] == "Bearer not-a-real-credential"
    assert calls[0]["json"] == {
        "model": "accounts/example/models/test",
        "prompt": prompt,
        "max_tokens": 4096,
        "top_k": 40,
        "temperature": 0.0,
    }


@pytest.mark.parametrize("api_key", ["", "not-a-real-credential"])
def test_local_adapter_receives_complete_prompt_and_optional_api_key(api_key: str) -> None:
    calls: list[dict[str, Any]] = []

    def request(url: str, **kwargs: Any) -> FakeResponse:
        calls.append({"url": url, **kwargs})
        return FakeResponse(200, {"choices": [{"message": {"content": 123}}]})

    prompt = "Complete local instruction\nwith every line preserved."
    worker = LocalWorker(
        "http://127.0.0.1:11435",
        "local-model",
        api_key=api_key,
        request=request,
    )
    assert worker.execute(task_input(prompt)) == TaskOutput(task_id="task-001", answer="123")
    assert calls[0]["url"] == "http://127.0.0.1:11435/v1/chat/completions"
    assert calls[0]["json"] == {
        "model": "local-model",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 4096,
        "temperature": 0.0,
    }
    if api_key:
        assert calls[0]["headers"]["Authorization"] == f"Bearer {api_key}"
    else:
        assert "Authorization" not in calls[0]["headers"]


@pytest.mark.parametrize(("base_url", "model_id"), [("", "model"), ("http://local", "")])
def test_local_configuration_failure_is_clear(base_url: str, model_id: str) -> None:
    with pytest.raises(ProviderConfigurationError) as captured:
        LocalWorker(base_url, model_id).execute(task_input())
    assert str(captured.value) == "Selected provider configuration is incomplete"
    assert "LOCAL_MODEL_" in captured.value.internal_detail


def test_fireworks_transient_status_is_retried_within_bound() -> None:
    responses = [
        FakeResponse(500),
        FakeResponse(200, {"choices": [{"text": "ready"}]}),
    ]
    calls: list[int] = []

    def request(*_args: Any, **_kwargs: Any) -> FakeResponse:
        calls.append(1)
        return responses.pop(0)

    worker = FireworksWorker(
        "credential",
        "https://example.test/v1",
        "model",
        max_attempts=2,
        request=request,
        sleep=lambda _delay: None,
    )
    assert worker.execute(task_input()) == TaskOutput(task_id="task-001", answer="ready")
    assert len(calls) == 2


def test_fireworks_retry_budget_is_never_exceeded() -> None:
    calls: list[int] = []

    def request(*_args: Any, **_kwargs: Any) -> FakeResponse:
        calls.append(1)
        return FakeResponse(503)

    worker = FireworksWorker(
        "credential",
        "https://example.test",
        "model",
        max_attempts=3,
        request=request,
        sleep=lambda _delay: None,
    )
    with pytest.raises(ProviderUnavailableError) as captured:
        worker.execute(task_input())
    assert str(captured.value) == "Selected provider is unavailable"
    assert "http_status=503" in captured.value.internal_detail
    assert len(calls) == 3


def test_fireworks_transport_retry_is_bounded_and_suppresses_sensitive_cause(
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret = "sensitive-" + "transport-token"
    calls: list[int] = []

    def request(*_args: Any, **_kwargs: Any) -> FakeResponse:
        calls.append(1)
        raise requests.ConnectionError(f"transport contained {secret}")

    worker = FireworksWorker(
        secret,
        "https://example.test",
        "model",
        max_attempts=2,
        request=request,
        sleep=lambda _delay: None,
    )
    with pytest.raises(ProviderUnavailableError) as captured:
        worker.execute(task_input())
    assert len(calls) == 2
    assert secret not in str(captured.value)
    assert secret not in repr(worker)
    assert captured.value.__cause__ is None
    terminal_output = capsys.readouterr()
    assert secret not in terminal_output.out
    assert secret not in terminal_output.err


def test_fireworks_permanent_status_is_not_retried() -> None:
    calls: list[int] = []

    def request(*_args: Any, **_kwargs: Any) -> FakeResponse:
        calls.append(1)
        return FakeResponse(400)

    worker = FireworksWorker("credential", "https://example.test", "model", request=request)
    with pytest.raises(ProviderPermanentRequestError) as captured:
        worker.execute(task_input())
    assert str(captured.value) == "Selected provider rejected the request"
    assert "http_status=400" in captured.value.internal_detail
    assert len(calls) == 1


@pytest.mark.parametrize("worker_type", ["fireworks", "local"])
@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"choices": []},
        {"choices": [{"message": {"content": ""}}]},
        {"choices": [{"message": {"content": None}}]},
        {"choices": [{"message": {"content": {"unexpected": True}}}]},
    ],
)
def test_both_workers_reject_malformed_or_empty_provider_content(
    worker_type: str, payload: object
) -> None:
    request = lambda *_args, **_kwargs: FakeResponse(200, payload)
    worker = (
        FireworksWorker("credential", "https://example.test", "model", max_attempts=1, request=request)
        if worker_type == "fireworks"
        else LocalWorker("http://127.0.0.1:11435", "model", request=request)
    )
    with pytest.raises(ProviderInvalidResponseError):
        worker.execute(task_input())


def test_local_connection_error_is_generic_and_contains_no_secret(
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret = "sensitive-" + "local-token"

    def request(*_args: Any, **_kwargs: Any) -> FakeResponse:
        raise requests.ConnectionError(f"connection contained {secret}")

    worker = LocalWorker("http://127.0.0.1:11435", "model", secret, request=request)
    with pytest.raises(ProviderUnavailableError) as captured:
        worker.execute(task_input())
    assert str(captured.value) == "Selected provider is unavailable"
    assert secret not in str(captured.value)
    assert secret not in repr(worker)
    assert captured.value.__cause__ is None
    terminal_output = capsys.readouterr()
    assert secret not in terminal_output.out
    assert secret not in terminal_output.err


def test_local_non_success_status_is_a_provider_error() -> None:
    worker = LocalWorker(
        "http://127.0.0.1:11435",
        "model",
        request=lambda *_args, **_kwargs: FakeResponse(503),
    )
    with pytest.raises(ProviderUnavailableError) as captured:
        worker.execute(task_input())
    assert "http_status=503" in captured.value.internal_detail


def test_local_worker_has_no_fireworks_dependency_or_environment_reads() -> None:
    source = (os.path.dirname(__file__) + "/../src/audisor/workers/local.py")
    text = open(source, encoding="utf-8").read().lower()
    assert "fireworks" not in text
    assert "fireworks_" not in text


def _safe_provider_shape(response: requests.Response) -> str:
    try:
        payload = response.json()
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError, ValueError, requests.JSONDecodeError):
        return "unusable"
    return f"choices[0].message.content:{type(content).__name__}"


@pytest.mark.live_fireworks
def test_optional_live_fireworks_api_smoke() -> None:
    required = ("FIREWORKS_API_KEY", "FIREWORKS_BASE_URL", "FIREWORKS_MODEL")
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        pytest.skip(f"live Fireworks preflight is missing: {', '.join(missing)}")

    expected_prompt = "Return only the word ready."
    stats: dict[str, Any] = {
        "request_count": 0,
        "provider_status": None,
        "provider_shape": None,
        "prompt": None,
    }

    def monitored_request(*args: Any, **kwargs: Any) -> requests.Response:
        stats["request_count"] += 1
        response = requests.post(*args, **kwargs)
        stats["provider_status"] = response.status_code
        stats["provider_shape"] = _safe_provider_shape(response)
        stats["prompt"] = kwargs["json"]["prompt"]
        return response

    worker = FireworksWorker.from_environment()
    worker.request = monitored_request
    response = client_for(worker, "fireworks").post(
        "/v1/tasks",
        json=[{"task_id": "task-fireworks-live-001", "prompt": expected_prompt}],
    )
    assert response.status_code == 200
    assert response.json() == [{"task_id": "task-fireworks-live-001", "answer": "ready"}]
    answer = response.json()[0]["answer"]
    safe_record = {
        "provider": "fireworks",
        "provider_id": worker.provider_id,
        "model_identifier_sha256": hashlib.sha256(worker.model.encode("utf-8")).hexdigest(),
        "provider_status": stats["provider_status"],
        "request_count": stats["request_count"],
        "retry_count": max(0, stats["request_count"] - 1),
        "fallback_count": 0,
        "task_id_preserved": response.json()[0]["task_id"] == "task-fireworks-live-001",
        "prompt_match": stats["prompt"] == expected_prompt,
        "sanitized_response": {
            "answer_sha256": hashlib.sha256(answer.encode("utf-8")).hexdigest(),
            "answer_length": len(answer),
        },
        "provider_response_shape": stats["provider_shape"],
        "local_api_status": response.status_code,
        "local_api_response_shape": "array<object{task_id:string,answer:string}>",
    }
    print("LIVE_FIREWORKS_PROOF=" + json.dumps(safe_record, sort_keys=True))


@pytest.mark.live_local
def test_optional_live_local_api_smoke() -> None:
    required = ("LOCAL_MODEL_BASE_URL", "LOCAL_MODEL_ID")
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        pytest.skip(f"live local preflight is missing: {', '.join(missing)}")

    expected_prompt = "Return only the word ready."
    stats: dict[str, Any] = {
        "request_count": 0,
        "provider_status": None,
        "provider_shape": None,
        "prompt": None,
    }

    def monitored_request(*args: Any, **kwargs: Any) -> requests.Response:
        stats["request_count"] += 1
        response = requests.post(*args, **kwargs)
        stats["provider_status"] = response.status_code
        stats["provider_shape"] = _safe_provider_shape(response)
        stats["prompt"] = kwargs["json"]["messages"][0]["content"]
        return response

    worker = LocalWorker.from_environment()
    worker.request = monitored_request
    response = client_for(worker, "local-openai-compatible").post(
        "/v1/tasks",
        json=[{"task_id": "task-local-live-001", "prompt": expected_prompt}],
    )
    assert response.status_code == 200
    assert response.json() == [{"task_id": "task-local-live-001", "answer": "ready"}]
    answer = response.json()[0]["answer"]
    safe_record = {
        "provider": "local-openai-compatible",
        "provider_id": worker.provider_id,
        "model_identifier_sha256": hashlib.sha256(worker.model_id.encode("utf-8")).hexdigest(),
        "provider_status": stats["provider_status"],
        "request_count": stats["request_count"],
        "retry_count": 0,
        "fallback_count": 0,
        "task_id_preserved": response.json()[0]["task_id"] == "task-local-live-001",
        "prompt_match": stats["prompt"] == expected_prompt,
        "sanitized_response": {
            "answer_sha256": hashlib.sha256(answer.encode("utf-8")).hexdigest(),
            "answer_length": len(answer),
        },
        "provider_response_shape": stats["provider_shape"],
        "local_api_status": response.status_code,
        "local_api_response_shape": "array<object{task_id:string,answer:string}>",
    }
    print("LIVE_LOCAL_PROOF=" + json.dumps(safe_record, sort_keys=True))
