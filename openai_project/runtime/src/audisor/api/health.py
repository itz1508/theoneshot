"""Process liveness and provider-neutral readiness endpoints."""

from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import APIRouter, Depends

from audisor.builder.store import BuildStore, BuildStoreError
from audisor.routing.configuration import get_provider_router
from audisor.routing.router import ProviderRouter
from audisor.schemas.health import HealthResponse, ProviderReadiness, ReadinessResponse
from audisor.workers.base import ProviderCapabilities, ProviderError

router = APIRouter(tags=["health"])


def _nearest_existing_parent(path: Path) -> Path | None:
    candidate = path
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    return candidate if candidate.exists() else None


def data_root_is_ready() -> bool:
    try:
        path = BuildStore.from_environment().data_dir
    except (BuildStoreError, OSError, ValueError):
        return False
    existing = _nearest_existing_parent(path)
    return bool(existing and existing.is_dir() and os.access(existing, os.W_OK))


def public_schemas_are_ready() -> bool:
    root = Path(__file__).resolve().parents[4] / "schemas"
    required = {
        "build-input.schema.json",
        "build-plan.schema.json",
        "build-execution-input.schema.json",
        "build-execution-state.schema.json",
        "task-input.schema.json",
        "task-output.schema.json",
        "worker-action-plan.schema.json",
    }
    try:
        present = {path.name for path in root.glob("*.schema.json")}
        if not required.issubset(present):
            return False
        for name in required:
            if not isinstance(json.loads((root / name).read_text(encoding="utf-8")), dict):
                return False
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    return True


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse()


@router.get("/ready", response_model=ReadinessResponse)
def ready(router: ProviderRouter = Depends(get_provider_router)) -> ReadinessResponse:
    data_ready = data_root_is_ready()
    schemas_ready = public_schemas_are_ready()
    selected = router.selected_provider_id
    if selected is None:
        provider = ProviderReadiness(
            selected=None,
            configuration="missing",
            capabilities_loaded=False,
        )
    else:
        try:
            selected_provider = router.select_provider()
            capabilities = selected_provider.capabilities()
            if not isinstance(capabilities, ProviderCapabilities):
                raise TypeError("provider capabilities must use the typed contract")
            configured = selected_provider.configuration_status()
            provider = ProviderReadiness(
                selected=selected,
                configuration="present" if configured else "missing",
                capabilities_loaded=capabilities is not None,
            )
        except (ProviderError, TypeError):
            provider = ProviderReadiness(
                selected=selected,
                configuration="invalid",
                capabilities_loaded=False,
            )
    status = (
        "ready"
        if provider.configuration == "present"
        and provider.capabilities_loaded
        and data_ready
        and schemas_ready
        else "degraded"
    )
    return ReadinessResponse(
        status=status,
        provider=provider,
        data_root_ready=data_ready,
        schemas_ready=schemas_ready,
    )
