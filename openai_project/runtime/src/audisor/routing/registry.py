"""Generic lazy provider registry."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping

from audisor.workers.base import ProviderConfigurationError, WorkerProvider

ProviderFactory = Callable[[], WorkerProvider]
_PROVIDER_ID = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{0,62}[a-z0-9])?$")


class ProviderRegistry:
    """Map provider IDs to lazy factories without constructing fallbacks."""

    def __init__(self, factories: Mapping[str, ProviderFactory] | None = None) -> None:
        self._factories: dict[str, ProviderFactory] = {}
        for provider_id, factory in (factories or {}).items():
            self.register(provider_id, factory)

    @property
    def provider_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._factories))

    def register(self, provider_id: str, factory: ProviderFactory) -> None:
        if not _PROVIDER_ID.fullmatch(provider_id):
            raise ProviderConfigurationError("Provider ID is invalid")
        if provider_id in self._factories:
            raise ProviderConfigurationError("Provider ID is already registered")
        if not callable(factory):
            raise ProviderConfigurationError("Provider factory is invalid")
        self._factories[provider_id] = factory

    def create(self, provider_id: str) -> WorkerProvider:
        if not _PROVIDER_ID.fullmatch(provider_id):
            raise ProviderConfigurationError("Configured provider ID is invalid")
        factory = self._factories.get(provider_id)
        if factory is None:
            raise ProviderConfigurationError(f"Unknown provider: {provider_id}")
        provider = factory()
        if provider.provider_id != provider_id:
            raise ProviderConfigurationError("Provider factory identity mismatch")
        return provider
