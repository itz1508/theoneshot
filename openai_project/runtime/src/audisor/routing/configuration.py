"""Adapter registration and normalized provider selection configuration."""

from __future__ import annotations

import os
from functools import lru_cache

from audisor.routing.registry import ProviderRegistry
from audisor.routing.router import ProviderRouter
from audisor.workers.fireworks import FireworksWorker
from audisor.workers.local import LocalWorker


def build_provider_registry() -> ProviderRegistry:
    return ProviderRegistry(
        {
            FireworksWorker.provider_id: FireworksWorker.from_environment,
            LocalWorker.provider_id: LocalWorker.from_environment,
        }
    )


@lru_cache(maxsize=1)
def get_provider_router() -> ProviderRouter:
    configured = os.environ.get("AUDISOR_PROVIDER")
    selected = configured.strip() if configured is not None and configured.strip() else None
    return ProviderRouter(selected, build_provider_registry())
