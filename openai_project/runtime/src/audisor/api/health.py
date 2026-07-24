"""Process liveness and tombstone readiness endpoints.

In 0.10.0 the legacy BYOK/BYOM runtime is tombstoned.  /health and /ready
return 200 with tombstone metadata so operational probes stay green while
every functional legacy route returns 410.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter(tags=["health"])


def _nearest_existing_parent(path: Path) -> Path | None:
    candidate = path
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    return candidate if candidate.exists() else None


def data_root_is_ready() -> bool:
    """Legacy compatibility helper — imported only when explicitly called."""
    from audisor.builder.store import BuildStore, BuildStoreError

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


@router.get("/health", deprecated=True)
def health() -> JSONResponse:
    return JSONResponse(
        status_code=200,
        content={
            "status": "deprecated",
            "serving_mode": "tombstone",
            "removal_version": "1.0.0",
        },
    )


@router.get("/ready", deprecated=True)
def ready() -> JSONResponse:
    return JSONResponse(
        status_code=200,
        content={
            "ready": True,
            "serving_mode": "tombstone",
            "legacy_runtime_available": False,
        },
    )
