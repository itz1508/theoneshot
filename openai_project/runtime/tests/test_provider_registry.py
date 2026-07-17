"""Provider selection, laziness, capability, and extension-boundary proofs."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from audisor.routing.registry import ProviderRegistry
from audisor.routing.router import ProviderRouter
from audisor.schemas.task_input import TaskInput
from audisor.schemas.task_output import TaskOutput
from audisor.service import TaskService
from audisor.workers.base import (
    ProviderCapabilities,
    ProviderCapabilityError,
    ProviderConfigurationError,
)


@dataclass
class StaticProvider:
    provider_id: str
    text: bool = True
    calls: int = 0

    def configuration_status(self) -> bool:
        return True

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(text=self.text)

    def execute(self, task: TaskInput) -> TaskOutput:
        self.calls += 1
        return TaskOutput(task_id=task.task_id, answer="ready")


def test_missing_selection_constructs_no_provider_and_has_no_default() -> None:
    constructed: list[str] = []
    registry = ProviderRegistry(
        {
            "fireworks": lambda: constructed.append("fireworks"),
            "local-openai-compatible": lambda: constructed.append("local"),
        }
    )
    with pytest.raises(ProviderConfigurationError, match="AUDISOR_PROVIDER"):
        ProviderRouter(None, registry).select_provider()
    assert constructed == []


def test_unknown_selection_is_deterministic_and_constructs_no_fallback() -> None:
    constructed: list[str] = []
    registry = ProviderRegistry(
        {
            "fireworks": lambda: constructed.append("fireworks"),
            "local-openai-compatible": lambda: constructed.append("local"),
        }
    )
    with pytest.raises(ProviderConfigurationError, match="Unknown provider: future"):
        ProviderRouter("future", registry).select_provider()
    assert constructed == []


def test_only_explicitly_selected_provider_is_constructed() -> None:
    constructed: list[str] = []
    selected = StaticProvider("local-openai-compatible")
    registry = ProviderRegistry(
        {
            "fireworks": lambda: constructed.append("fireworks"),
            "local-openai-compatible": lambda: (
                constructed.append("local-openai-compatible") or selected
            ),
        }
    )
    assert ProviderRouter("local-openai-compatible", registry).select_provider() is selected
    assert constructed == ["local-openai-compatible"]


def test_future_provider_registration_needs_no_core_service_change() -> None:
    future = StaticProvider("future-provider")
    service = TaskService(
        ProviderRouter("future-provider", ProviderRegistry({"future-provider": lambda: future}))
    )
    result = service.execute_tasks([TaskInput(task_id="task-001", prompt="work")])
    assert result == [TaskOutput(task_id="task-001", answer="ready")]
    assert future.calls == 1


def test_capability_rejection_occurs_before_provider_invocation() -> None:
    provider = StaticProvider("no-text", text=False)
    service = TaskService(ProviderRouter("no-text", ProviderRegistry({"no-text": lambda: provider})))
    with pytest.raises(ProviderCapabilityError) as captured:
        service.execute_tasks([TaskInput(task_id="task-001", prompt="work")])
    assert captured.value.internal_detail == "required=text"
    assert provider.calls == 0

