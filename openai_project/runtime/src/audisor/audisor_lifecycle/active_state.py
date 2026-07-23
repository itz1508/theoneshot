"""Runtime-owned active-state envelope writer.

The hook evaluator reads ``active-lock.json`` expecting a complete envelope::

    {
        "operation_id": "...",
        "primary_lock": { ... verified lock ... },
        "execution_contract": { ... verified contract ... },
        "drift_state": "valid"
    }

This module is the single authority for writing, reading, and clearing that
envelope.  Both the MCP server path and the existing ``plan_trigger`` path
must use these functions.

Fail-closed semantics: malformed or conflicting state is never silently
overwritten.  Cleanup requires an explicit call to :func:`clear_active_state`.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping

from .adapter import verify_contract
from .contract import AudisorLifecycleError, verify_lock

STATE_FILENAME = "active-lock.json"


def default_state_root() -> Path:
    """Default state directory: ``<repo_root>/.codex/audisor-state``."""
    return Path(__file__).resolve().parents[5] / ".codex" / "audisor-state"


def _state_path(state_root: Path) -> Path:
    return state_root / STATE_FILENAME


def write_active_state(
    state_root: Path,
    *,
    operation_id: str,
    primary_lock: Mapping[str, Any],
    execution_contract: Mapping[str, Any],
    drift_state: str = "valid",
) -> Path:
    """Atomically write a verified active-state envelope.

    Args:
        state_root: Directory that holds ``active-lock.json``.
        operation_id: Caller-supplied operation identifier (advisory metadata;
            not bound into the lock or contract formats in this increment).
        primary_lock: A lock that passes :func:`verify_lock`.
        execution_contract: A contract that passes :func:`verify_contract`.
        drift_state: Must be ``"valid"`` for a fresh envelope.

    Returns:
        The path to the written ``active-lock.json``.

    Raises:
        AudisorLifecycleError: If the lock or contract fail verification,
            if existing state is malformed, or if a conflicting active
            operation is present.
    """
    if not verify_lock(primary_lock):
        raise AudisorLifecycleError("Refusing to write state: lock verification failed")
    if not verify_contract(execution_contract):
        raise AudisorLifecycleError("Refusing to write state: contract verification failed")
    if drift_state != "valid":
        raise AudisorLifecycleError("Refusing to write state: drift_state must be 'valid' for a new envelope")

    path = _state_path(state_root)

    # Fail closed: if existing state is present, it must be explicitly cleared
    # before a new envelope can be written.  This prevents silent overwrite of
    # a concurrent or malformed operation.
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise AudisorLifecycleError(
                "Refusing to overwrite malformed active state; "
                "explicit clear_active_state() is required"
            ) from exc
        if isinstance(existing, Mapping):
            existing_op = existing.get("operation_id")
            if existing_op and existing_op != operation_id:
                raise AudisorLifecycleError(
                    f"Active state belongs to operation '{existing_op}'; "
                    f"cannot replace with '{operation_id}' without explicit clear"
                )
            # Same operation_id: idempotent rewrite is allowed
        else:
            raise AudisorLifecycleError(
                "Refusing to overwrite malformed active state; "
                "explicit clear_active_state() is required"
            )

    envelope = {
        "operation_id": operation_id,
        "primary_lock": dict(primary_lock),
        "execution_contract": dict(execution_contract),
        "drift_state": drift_state,
    }

    state_root.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(envelope, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(temporary, path)
    return path


def read_active_state(state_root: Path) -> Mapping[str, Any] | None:
    """Read the active-state envelope, returning ``None`` if absent.

    Raises:
        AudisorLifecycleError: If the file exists but is malformed.
    """
    path = _state_path(state_root)
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise AudisorLifecycleError("active state file is malformed") from exc
    if not isinstance(value, Mapping):
        raise AudisorLifecycleError("active state file is malformed")
    return value


def clear_active_state(state_root: Path) -> bool:
    """Explicitly remove the active-state envelope.

    Returns ``True`` if a file was removed, ``False`` if none existed.
    This is the only sanctioned way to remove active state.
    """
    path = _state_path(state_root)
    if path.exists():
        path.unlink()
        return True
    return False
