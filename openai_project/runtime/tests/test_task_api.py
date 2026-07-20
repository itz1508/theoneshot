"""HTTP validation, dispatch, and error contract tests."""

import json
import threading
import time
from pathlib import Path
from typing import Any

import pytest
import requests
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

from audisor.api.tasks import get_task_service
from audisor.main import create_app
from audisor.routing.configuration import get_provider_router
from audisor.routing.router import ProviderRouter
from audisor.service import TaskService
from audisor.schemas.task_input import TaskInput
from audisor.workers.base import ProviderInvalidResponseError, WorkerProvider
from audisor.workers.fireworks import FireworksWorker
from audisor.workers.local import LocalWorker

SCHEMA_ROOT = Path(__file__).resolve().parents[2] / "schemas"


class FakeWorker:
    name = "fake"

    def __init__(self, answer: object = "ready", error: Exception | None = None) -> None:
        self.answer = answer
        self.error = error
        self.calls: list[TaskInput] = []

    def execute(self, task: TaskInput) -> object:
        self.calls.append(task)
        if self.error:
            raise self.error
        return self.answer


class NeverWorker:
    name = "never"

    def __init__(self) -> None:
        self.calls: list[TaskInput] = []

    def execute(self, task: TaskInput) -> object:
        self.calls.append(task)
        raise AssertionError("unselected worker was called")


class FakeHttpResponse:
    def __init__(self, status_code: int, payload: Any = None) -> None:
        self.status_code = status_code
        self.payload = payload

    def json(self) -> Any:
        return self.payload


from provider_testkit import provider_router


def client_for_router(router: ProviderRouter) -> TestClient:
    app = create_app()
    service = TaskService(router, max_workers=2)
    app.dependency_overrides[get_task_service] = lambda: service
    return TestClient(app)


def client_for(worker: WorkerProvider, selected: str = "fireworks") -> TestClient:
    other = NeverWorker()
    router = (
        provider_router(selected, worker, other)
        if selected == "fireworks"
        else provider_router("local-openai-compatible", other, worker)
    )
    return client_for_router(router)


def error_validator() -> Draft202012Validator:
    schema = json.loads((SCHEMA_ROOT / "error.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def test_one_valid_task_succeeds_and_preserves_complete_prompt() -> None:
    worker = FakeWorker("ready")
    response = client_for(worker).post(
        "/v1/tasks",
        json=[{"task_id": "task-001", "prompt": "Return the word ready."}],
    )
    assert response.status_code == 200
    assert response.json() == [{"task_id": "task-001", "answer": "ready"}]
    assert [task.prompt for task in worker.calls] == ["Return the word ready."]


@pytest.mark.parametrize(
    "payload",
    [
        {"task_id": "not-an-array", "prompt": "x"},
        [],
        [{"prompt": "missing id"}],
        [{"task_id": "", "prompt": "empty id"}],
        [{"task_id": "id", "prompt": "   "}],
        [{"task_id": "id", "prompt": 3}],
    ],
)
def test_invalid_input_returns_422_without_worker_execution(payload: object) -> None:
    worker = FakeWorker()
    response = client_for(worker).post("/v1/tasks", json=payload)
    assert response.status_code == 422
    assert worker.calls == []
    error_validator().validate(response.json())


def test_duplicate_task_ids_return_422_without_worker_execution() -> None:
    worker = FakeWorker()
    response = client_for(worker).post(
        "/v1/tasks",
        json=[
            {"task_id": "same", "prompt": "one"},
            {"task_id": "same", "prompt": "two"},
        ],
    )
    assert response.status_code == 422
    assert worker.calls == []
    error_validator().validate(response.json())


def test_provider_failure_returns_schema_valid_502() -> None:
    worker = FakeWorker(
        error=ProviderInvalidResponseError("Selected provider returned an invalid response")
    )
    response = client_for(worker).post(
        "/v1/tasks",
        json=[{"task_id": "task-001", "prompt": "work"}],
    )
    assert response.status_code == 502
    assert response.json() == {
        "detail": {
            "code": "provider_invalid_response",
            "message": "Selected provider returned an invalid response",
        }
    }
    error_validator().validate(response.json())


def test_invalid_provider_selection_returns_503_before_any_dispatch() -> None:
    fireworks = FakeWorker()
    local = FakeWorker()
    response = client_for_router(provider_router("unsupported", fireworks, local)).post(
        "/v1/tasks",
        json=[{"task_id": "task-001", "prompt": "work"}],
    )
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "provider_configuration_error"
    assert fireworks.calls == []
    assert local.calls == []
    error_validator().validate(response.json())


def test_unsupported_environment_selection_fails_inside_api_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AUDISOR_PROVIDER", "unsupported")
    get_task_service.cache_clear()
    get_provider_router.cache_clear()
    try:
        response = TestClient(create_app()).post(
            "/v1/tasks",
            json=[{"task_id": "task-001", "prompt": "work"}],
        )
    finally:
        get_task_service.cache_clear()
        get_provider_router.cache_clear()
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "provider_configuration_error"
    error_validator().validate(response.json())


def test_local_environment_selection_reaches_only_local_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AUDISOR_PROVIDER", "local-openai-compatible")
    monkeypatch.delenv("LOCAL_MODEL_BASE_URL", raising=False)
    monkeypatch.delenv("LOCAL_MODEL_ID", raising=False)
    get_task_service.cache_clear()
    get_provider_router.cache_clear()
    try:
        response = TestClient(create_app()).post(
            "/v1/tasks",
            json=[{"task_id": "task-local-001", "prompt": "work"}],
        )
    finally:
        get_task_service.cache_clear()
        get_provider_router.cache_clear()
    assert response.status_code == 503
    assert response.json()["detail"] == {
        "code": "provider_configuration_error",
        "message": "Selected provider configuration is incomplete",
    }
    error_validator().validate(response.json())


def test_local_configuration_error_does_not_fall_back_to_fireworks() -> None:
    fireworks = FakeWorker("silent fallback would be incorrect")
    local = LocalWorker("", "")
    response = client_for_router(provider_router("local-openai-compatible", fireworks, local)).post(
        "/v1/tasks",
        json=[{"task_id": "task-local-001", "prompt": "work"}],
    )
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "provider_configuration_error"
    assert fireworks.calls == []
    error_validator().validate(response.json())


def test_fireworks_configuration_error_does_not_fall_back_to_local() -> None:
    fireworks = FireworksWorker("", "", "")
    local = FakeWorker("silent fallback would be incorrect")
    response = client_for_router(provider_router("fireworks", fireworks, local)).post(
        "/v1/tasks",
        json=[{"task_id": "task-fireworks-001", "prompt": "work"}],
    )
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "provider_configuration_error"
    assert local.calls == []
    error_validator().validate(response.json())


@pytest.mark.parametrize("selected", ["fireworks", "local-openai-compatible"])
def test_both_real_adapters_produce_same_amd_compatible_api_shape(selected: str) -> None:
    calls: list[str] = []

    def request(_url: str, **kwargs: Any) -> FakeHttpResponse:
        body = kwargs["json"]
        calls.append(body["prompt"] if "prompt" in body else body["messages"][0]["content"])
        response = {"choices": [{"text": 123}]} if selected == "fireworks" else {"choices": [{"message": {"content": 123}}]}
        return FakeHttpResponse(200, response)

    worker: WorkerProvider = (
        FireworksWorker("credential", "https://example.test", "model", request=request)
        if selected == "fireworks"
        else LocalWorker("http://127.0.0.1:11435", "model", request=request)
    )
    prompt = "Complete prompt\nwith all content preserved."
    response = client_for(worker, selected).post(
        "/v1/tasks",
        json=[{"task_id": f"task-{selected}-001", "prompt": prompt}],
    )
    assert response.status_code == 200
    assert response.json() == [{"task_id": f"task-{selected}-001", "answer": "123"}]
    assert calls == [prompt]


@pytest.mark.parametrize("selected", ["fireworks", "local-openai-compatible"])
def test_both_real_adapters_preserve_batch_ids_and_input_order(selected: str) -> None:
    lock = threading.Lock()
    active = 0
    peak = 0

    def request(_url: str, **kwargs: Any) -> FakeHttpResponse:
        nonlocal active, peak
        body = kwargs["json"]
        prompt = body["prompt"] if "prompt" in body else body["messages"][0]["content"]
        delay, answer = prompt.split("|", 1)
        with lock:
            active += 1
            peak = max(peak, active)
        try:
            time.sleep(float(delay))
            response = {"choices": [{"text": answer}]} if selected == "fireworks" else {"choices": [{"message": {"content": answer}}]}
            return FakeHttpResponse(200, response)
        finally:
            with lock:
                active -= 1

    worker: WorkerProvider = (
        FireworksWorker("credential", "https://example.test", "model", request=request)
        if selected == "fireworks"
        else LocalWorker("http://127.0.0.1:11435", "model", request=request)
    )
    response = client_for(worker, selected).post(
        "/v1/tasks",
        json=[
            {"task_id": "first", "prompt": "0.06|one"},
            {"task_id": "second", "prompt": "0.01|two"},
            {"task_id": "third", "prompt": "0.02|three"},
        ],
    )
    assert response.status_code == 200
    assert response.json() == [
        {"task_id": "first", "answer": "one"},
        {"task_id": "second", "answer": "two"},
        {"task_id": "third", "answer": "three"},
    ]
    assert 1 < peak <= 2


def test_local_connection_failure_returns_503_without_sensitive_cause() -> None:
    secret = "sensitive-" + "connection-token"

    def request(*_args: Any, **_kwargs: Any) -> FakeHttpResponse:
        raise requests.ConnectionError(f"connection included {secret}")

    worker = LocalWorker("http://127.0.0.1:11435", "model", secret, request=request)
    response = client_for(worker, "local-openai-compatible").post(
        "/v1/tasks",
        json=[{"task_id": "task-local-001", "prompt": "work"}],
    )
    serialized = response.text
    assert response.status_code == 503
    assert response.json() == {
        "detail": {
            "code": "provider_unavailable",
            "message": "Selected provider is unavailable",
        }
    }
    assert secret not in serialized
    assert secret not in repr(worker)
    error_validator().validate(response.json())


@pytest.mark.parametrize("selected", ["fireworks", "local-openai-compatible"])
def test_empty_provider_content_returns_502_for_both_adapters(selected: str) -> None:
    request = lambda *_args, **_kwargs: FakeHttpResponse(
        200, {"choices": [{"message": {"content": ""}}]}
    )
    worker: WorkerProvider = (
        FireworksWorker("credential", "https://example.test", "model", request=request)
        if selected == "fireworks"
        else LocalWorker("http://127.0.0.1:11435", "model", request=request)
    )
    response = client_for(worker, selected).post(
        "/v1/tasks",
        json=[{"task_id": f"task-{selected}-001", "prompt": "work"}],
    )
    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "provider_invalid_response"
    error_validator().validate(response.json())
