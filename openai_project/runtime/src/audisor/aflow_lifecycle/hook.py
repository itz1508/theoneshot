"""Codex PreToolUse guard for mutation attempts lacking a verified A-Flow lock."""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Mapping

from .contract import verify_lock


MUTATING_TOOL_NAMES = {"applypatch", "edit", "write", "writefile", "filesystemwrite"}
MUTATING_COMMAND = re.compile(
    r"(?:apply_patch|set-content|out-file|add-content|new-item|remove-item|move-item|copy-item|git\s+(?:add|commit|reset|checkout)|>\s*[^&|])",
    re.IGNORECASE,
)


def default_state_root() -> Path:
    """Locate the project-level state root even when ``uv --directory`` changes CWD."""
    return Path(__file__).resolve().parents[5] / ".codex" / "aflow-state"


def _text(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)


def is_mutation_attempt(payload: Mapping[str, Any]) -> bool:
    name = str(payload.get("tool_name") or payload.get("tool") or "").replace("_", "").lower()
    if name in MUTATING_TOOL_NAMES:
        return True
    if name in {"bash", "shell", "command", "powershell"}:
        source = payload.get("tool_input") or payload.get("input") or payload.get("command") or ""
        return bool(MUTATING_COMMAND.search(_text(source)))
    return False


def _load_lock(state_root: Path) -> Mapping[str, Any] | None:
    try:
        value = json.loads((state_root / "active-lock.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    return value if isinstance(value, dict) else None


def evaluate_hook_payload(payload: Mapping[str, Any], state_root: Path) -> dict[str, Any]:
    """Return the documented Codex hook denial shape only when needed."""
    if not is_mutation_attempt(payload):
        return {}
    lock = _load_lock(state_root)
    if lock is not None and verify_lock(lock):
        return {}
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": "A qualifying repository mutation requires a verified primary-owned A-Flow lock.",
        }
    }


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise ValueError("hook payload must be an object")
    except (ValueError, json.JSONDecodeError):
        print(json.dumps({"decision": "block", "reason": "Malformed A-Flow hook input."}))
        return 0
    configured = os.environ.get("AFLOW_STATE_ROOT")
    state = Path(configured) if configured else default_state_root()
    result = evaluate_hook_payload(payload, state)
    if result:
        print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
