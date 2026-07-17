"""Typed deterministic providers for provider-neutral core tests."""

from __future__ import annotations

from dataclasses import dataclass

from audisor.routing.registry import ProviderRegistry
from audisor.routing.router import ProviderRouter
from audisor.schemas.task_input import TaskInput
from audisor.schemas.task_output import TaskOutput
from audisor.workers.base import ProviderCapabilities


@dataclass
class DelegatingProvider:
    provider_id: str
    delegate: object

    def configuration_status(self) -> bool:
        method = getattr(self.delegate, "configuration_status", None)
        return bool(method()) if callable(method) else True

    def capabilities(self) -> ProviderCapabilities:
        method = getattr(self.delegate, "capabilities", None)
        return method() if callable(method) else ProviderCapabilities(text=True)

    def execute(self, task: TaskInput) -> TaskOutput:
        result = self.delegate.execute(task)
        if isinstance(result, TaskOutput):
            return result
        if isinstance(result, str):
            return TaskOutput(task_id=task.task_id, answer=result)
        if isinstance(result, dict):
            return TaskOutput.model_validate(result)
        return result


def provider_router(
    selected: str | None,
    fireworks: object | None = None,
    local: object | None = None,
    extra: dict[str, object] | None = None,
) -> ProviderRouter:
    factories = {}
    if fireworks is not None:
        provider = DelegatingProvider("fireworks", fireworks)
        factories[provider.provider_id] = lambda provider=provider: provider
    if local is not None:
        provider = DelegatingProvider("local-openai-compatible", local)
        factories[provider.provider_id] = lambda provider=provider: provider
    for provider_id, delegate in (extra or {}).items():
        provider = DelegatingProvider(provider_id, delegate)
        factories[provider_id] = lambda provider=provider: provider
    return ProviderRouter(selected, ProviderRegistry(factories))
