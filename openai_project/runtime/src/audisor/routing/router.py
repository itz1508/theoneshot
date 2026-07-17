"""Provider-neutral selection with no implicit default or fallback."""

from __future__ import annotations

from dataclasses import dataclass

from audisor.routing.registry import ProviderRegistry
from audisor.workers.base import ProviderConfigurationError, WorkerProvider


@dataclass(frozen=True)
class ProviderRouter:
    selected_provider_id: str | None
    registry: ProviderRegistry

    def select_provider(self) -> WorkerProvider:
        if self.selected_provider_id is None:
            raise ProviderConfigurationError("AUDISOR_PROVIDER is not configured")
        return self.registry.create(self.selected_provider_id)
