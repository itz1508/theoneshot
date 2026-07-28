"""Persisted lifecycle results: state root, atomic writes, bounded reads.

This module lives beside ``artifact_flow`` so ``default_state_root``'s
relative resolution (``parents[5]`` → repository root) is unchanged.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .stage_contracts import RESULT_STATUSES


class PersistedResultError(RuntimeError):
    """The newest persisted result for an artifact is corrupt or incomplete.

    Never treated as a completed outcome: callers surface a bounded error
    instead of silently replaying or ignoring damaged state. Carries the
    offending state path and the failure kind (``corrupt`` | ``incomplete``)
    so the error result is a structured diagnostic, not just prose.
    """

    def __init__(self, message: str, *, state_path: str, failure: str) -> None:
        super().__init__(message)
        self.state_path = state_path
        self.failure = failure


def default_state_root() -> Path:
    """Default state directory: ``<repo_root>/.codex/audisor-state``.

    ``AUDISOR_STATE_ROOT`` (or ``AFLOW_STATE_ROOT``) overrides the location,
    matching the environment contract of the MCP server host.
    """
    env = os.environ.get("AUDISOR_STATE_ROOT") or os.environ.get("AFLOW_STATE_ROOT")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[5] / ".codex" / "audisor-state"


def _sanitize_artifact_id(artifact_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "-", artifact_id)[:80]


def _persist_result(state_root: Path, artifact_id: str, result: Mapping[str, Any]) -> Path | None:
    """Atomically write the cycle result; persistence must not alter the result."""
    try:
        artifacts_dir = state_root / "artifacts"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        path = artifacts_dir / f"{_sanitize_artifact_id(artifact_id)}-{stamp}.json"
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        os.replace(temporary, path)
        return path
    except OSError:
        return None


def read_last_result(artifact_id: str, state_root: Path | None = None) -> Mapping[str, Any] | None:
    """Most recent persisted cycle result for an artifact, if any.

    Raises :class:`PersistedResultError` when the newest matching file is
    unreadable, not a result object, or lacks a valid status — corrupt state
    is a bounded error, never silently skipped or treated as completed.
    """
    if not isinstance(artifact_id, str) or not artifact_id.strip():
        return None
    root = (state_root or default_state_root()) / "artifacts"
    if not root.is_dir():
        return None
    prefix = _sanitize_artifact_id(artifact_id) + "-"
    candidates = sorted(
        (path for path in root.iterdir() if path.name.startswith(prefix) and path.suffix == ".json"),
        key=lambda path: path.name,
        reverse=True,
    )
    for path in candidates:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PersistedResultError(
                f"persisted result {path.name} is corrupt: {exc}",
                state_path=str(path),
                failure="corrupt",
            ) from None
        if not isinstance(data, dict):
            raise PersistedResultError(
                f"persisted result {path.name} is not a result object",
                state_path=str(path),
                failure="corrupt",
            )
        if data.get("artifact_id") != artifact_id:
            # Sanitized-prefix collision with a different artifact: skip.
            continue
        if data.get("status") not in RESULT_STATUSES:
            raise PersistedResultError(
                f"persisted result {path.name} is incomplete: missing or invalid status",
                state_path=str(path),
                failure="incomplete",
            )
        return data
    return None
