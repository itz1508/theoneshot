"""Active-run guard: marker files that make concurrent identical runs visible."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .persistence import _sanitize_artifact_id

#: A fresh active-run marker older than this is a crashed run, not an active
#: one; matches the MCP server's tool timeout ceiling.
_ACTIVE_RUN_TTL_SECONDS = 900.0


def _active_run_marker_path(state_root: Path, artifact_id: str) -> Path:
    return state_root / "artifacts" / f"{_sanitize_artifact_id(artifact_id)}.running"


def _guard_active_run(
    root: Path,
    artifact_id: str,
    digest: str,
    run_id: str,
    started_at: str,
) -> tuple[dict[str, Any] | None, Path | None]:
    """Active-run guard: an identical submission while a prior run is still
    active returns that run's identity instead of starting another. The
    stdio server serializes calls, so a fresh marker from another process
    is the only way to get here; a stale marker is a crashed run.

    Returns ``(error_result, marker_path)``: an error result and no marker
    when an identical run is active, otherwise no error and the written
    marker (``None`` when the state directory is unwritable).
    """
    marker = _active_run_marker_path(root, artifact_id)
    try:
        if marker.is_file():
            age = time.time() - marker.stat().st_mtime
            try:
                active = json.loads(marker.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                active = {}
            if age < _ACTIVE_RUN_TTL_SECONDS and active.get("submission_digest") == digest:
                active_run_id = active.get("lifecycle_run_id", "unknown")
                return (
                    {
                        "status": "error",
                        "stage": "active_run",
                        "detail": (
                            f"an identical submission is already running "
                            f"(lifecycle_run_id {active_run_id}); reattach with "
                            "aflow_last_result after it finishes"
                        ),
                        "artifact_id": artifact_id,
                        "submission_digest": digest,
                        "lifecycle_run_id": active_run_id,
                    },
                    None,
                )
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(
            json.dumps(
                {
                    "lifecycle_run_id": run_id,
                    "submission_digest": digest,
                    "started_at": started_at,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return None, marker
    except OSError:
        # The marker is a guard, not a gate: state-dir trouble must not
        # break the lifecycle itself.
        return None, None
