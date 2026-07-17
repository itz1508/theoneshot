"""Shared typed-contract conformance checks for every shipped provider adapter."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
import requests

from audisor.schemas.task_input import TaskInput
from audisor.schemas.task_output import TaskOutput
from audisor.workers.base import (
    ProviderCapabilities,
    ProviderInvalidResponseError,
    ProviderTimeoutError,
    WorkerProvider,
)
from audisor.workers.fireworks import FireworksWorker
from audisor.workers.local import LocalWorker


class FakeResponse:
    def __init__(self, status_code: int, payload: Any = None) -> None:
        self.status_code = status_code
        self.payload = payload

    def json(self) -> Any:
        return self.payload


class FakeProvider:
    provider_id = "fake"

    def __init__(self, request: Callable[..., FakeResponse]) -> None:
        self.request = request

    def configuration_status(self) -> bool:
        return True

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(text=True)

    def execute(self, task: TaskInput) -> TaskOutput:
        try:
            response = self.request(
                "fake://provider",
                json={"messages": [{"role": "user", "content": task.prompt}]},
            )
        except requests.Timeout:
            raise ProviderTimeoutError("Selected provider request timed out") from None
        try:
            content = response.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, ValueError):
            raise ProviderInvalidResponseError(
                "Selected provider returned an invalid response"
            ) from None
        return TaskOutput(task_id=task.task_id, answer=str(content))


def adapter(provider_id: str, request: Callable[..., FakeResponse]) -> WorkerProvider:
    if provider_id == "fake":
        return FakeProvider(request)
    if provider_id == "fireworks":
        return FireworksWorker(
            "credential",
            "https://provider.example",
            "opaque-model",
            max_attempts=1,
            request=request,
        )
    return LocalWorker(
        "http://127.0.0.1:11435",
        "opaque-model",
        request=request,
    )


@pytest.mark.parametrize("provider_id", ["fake", "fireworks", "local-openai-compatible"])
def test_shipped_adapter_success_contract(provider_id: str) -> None:
    prompts: list[str] = []
    endpoints: list[str] = []

    def request(url: str, **kwargs: Any) -> FakeResponse:
        endpoints.append(url)
        prompts.append(kwargs["json"]["messages"][0]["content"])
        return FakeResponse(200, {"choices": [{"message": {"content": "ready"}}]})

    provider = adapter(provider_id, request)
    task = TaskInput(task_id="task-001", prompt="complete prompt")
    assert provider.provider_id == provider_id
    assert provider.configuration_status() is True
    assert provider.capabilities() == ProviderCapabilities(text=True)
    assert provider.execute(task) == TaskOutput(task_id="task-001", answer="ready")
    assert prompts == ["complete prompt"]
    assert endpoints == {
        "fake": ["fake://provider"],
        "fireworks": ["https://provider.example/v1/chat/completions"],
        "local-openai-compatible": ["http://127.0.0.1:11435/v1/chat/completions"],
    }[provider_id]


@pytest.mark.parametrize("provider_id", ["fake", "fireworks", "local-openai-compatible"])
def test_shipped_adapter_timeout_contract(provider_id: str) -> None:
    def request(*_args: Any, **_kwargs: Any) -> FakeResponse:
        raise requests.Timeout("sensitive upstream detail")

    with pytest.raises(ProviderTimeoutError) as captured:
        adapter(provider_id, request).execute(TaskInput(task_id="task-001", prompt="work"))
    assert str(captured.value) == "Selected provider request timed out"
    assert "sensitive" not in captured.value.internal_detail


@pytest.mark.parametrize("provider_id", ["fake", "fireworks", "local-openai-compatible"])
def test_shipped_adapter_invalid_response_contract(provider_id: str) -> None:
    request = lambda *_args, **_kwargs: FakeResponse(200, {"unexpected": True})
    with pytest.raises(ProviderInvalidResponseError) as captured:
        adapter(provider_id, request).execute(TaskInput(task_id="task-001", prompt="work"))
    assert str(captured.value) == "Selected provider returned an invalid response"
