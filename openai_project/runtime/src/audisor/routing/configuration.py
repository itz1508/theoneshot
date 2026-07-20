"""Adapter registration and normalized provider selection configuration."""

from __future__ import annotations

import os
from functools import lru_cache

from audisor.config import AudisorConfigError, load_provider_config
from audisor.routing.registry import ProviderRegistry
from audisor.routing.router import ProviderRouter
from audisor.workers.fireworks import FireworksWorker
from audisor.workers.local import LocalWorker


def build_provider_registry(provider_config: dict[str, str] | None = None) -> ProviderRegistry:
    local_factory = LocalWorker.from_environment
    if provider_config is not None:
        local_factory = lambda: LocalWorker(
            base_url=provider_config["base_url"],
            model_id=provider_config["model_id"],
            structured_output=True,
        )
    return ProviderRegistry(
        {
            FireworksWorker.provider_id: FireworksWorker.from_environment,
            LocalWorker.provider_id: local_factory,
        }
    )


@lru_cache(maxsize=1)
def get_provider_router() -> ProviderRouter:
    configured = os.environ.get("AUDISOR_PROVIDER")
    if configured is not None:
        if not configured.strip():
            from audisor.workers.base import ProviderConfigurationError
            raise ProviderConfigurationError(
                "Configured provider selection is empty",
                internal_detail="source=environment; field=AUDISOR_PROVIDER; expected=non-empty provider ID",
            )
        selected = configured.strip()
        return ProviderRouter(selected, build_provider_registry())
    try:
        persisted = load_provider_config()
    except AudisorConfigError as exc:
        raise
    if persisted is not None:
        return ProviderRouter(persisted["provider"], build_provider_registry(persisted))
    return ProviderRouter(None, build_provider_registry())
