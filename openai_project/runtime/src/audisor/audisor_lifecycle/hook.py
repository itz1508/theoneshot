"""Auditable Audisor PreToolUse control for Codex-interceptable mutations.

Protocol (Codex 0.144.6):
  - Allow: exit 0, no stdout.
  - Deny:  exit 0, stdout = {"hookSpecificOutput": {"hookEventName": "PreToolUse",
             "permissionDecision": "deny", "permissionDecisionReason": "..."}}
  - Audit/output failure: exit 2, stderr = reason.
  - Exit 1 is NEVER used.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from audisor.security.path_security import check_paths_allowed

from .adapter import verify_contract
from .contract import AudisorLifecycleError, canonical_text, verify_lock

# ---------------------------------------------------------------------------
# Exception hierarchy
# ---------------------------------------------------------------------------


class HookInputError(Exception):
    """Malformed stdin payload. Result: structured deny, exit 0."""


class HookVerificationError(Exception):
    """Contract/lock verification failed internally. Result: structured deny, exit 0."""


class HookAuditError(Exception):
    """Audit directory/file write failed. Result: stderr, exit 2."""


class HookOutputError(Exception):
    """JSON serialization or stdout write failed. Result: stderr, exit 2."""


# ---------------------------------------------------------------------------
# Detection patterns
# ---------------------------------------------------------------------------

MUTATING_TOOL_NAMES = {"applypatch", "edit", "write", "writefile", "filesystemwrite"}
MUTATING_COMMAND = re.compile(r"(?:apply_patch|set-content|out-file|add-content|new-item|remove-item|move-item|copy-item|git\s+(?:add|commit|reset|checkout)|>\s*[^&|])", re.I)
PATCH_TARGET = re.compile(r"(?:^\+\+\+ b/|^(?:\*\*\* (?:Add|Update|Delete) File: ))([^\r\n]+)", re.M)
PATH_IN_COMMAND = re.compile(r"(?<![\w.-])(openai_project/[\w./-]+)")


def default_state_root() -> Path:
    return Path(__file__).resolve().parents[5] / ".codex" / "audisor-state"


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


def _normal_path(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    path = value.replace("\\", "/")
    if not path:
        return None
    if ":" in path or path.startswith("/"):
        try:
            repo_root = Path(__file__).resolve().parents[5]
            path = Path(value).resolve().relative_to(repo_root).as_posix()
        except (OSError, ValueError):
            return None
    if path.startswith("./") or any(part in {"", ".", ".."} for part in path.split("/")):
        return None
    return path


def requested_targets(payload: Mapping[str, Any]) -> list[str] | None:
    raw = payload.get("requested_targets")
    if raw is None:
        source = payload.get("tool_input") or payload.get("input") or payload.get("command") or ""
        if isinstance(source, Mapping):
            # Codex wire format: Bash and apply_patch put content in tool_input.command
            command = source.get("command")
            if isinstance(command, str):
                raw = PATCH_TARGET.findall(command) or PATH_IN_COMMAND.findall(command)
            else:
                raw = source.get("requested_targets") or source.get("paths") or ([source["path"]] if "path" in source else None)
        else:
            text = _text(source)
            raw = PATCH_TARGET.findall(text) or PATH_IN_COMMAND.findall(text)
    if not isinstance(raw, list) or not raw:
        return None
    normalized = [_normal_path(item) for item in raw]
    return normalized if all(normalized) else None


def _ready_contract(contract: Mapping[str, Any]) -> bool:
    readiness = contract.get("readiness")
    gates = readiness.get("execution_permitted_when") if isinstance(readiness, Mapping) else None
    return verify_contract(contract) and isinstance(readiness, Mapping) and readiness.get("aflow_decision") == "no_material_gap" and readiness.get("contract_decision") == "no_material_gap" and readiness.get("unresolved_items") == [] and isinstance(gates, Mapping) and all(value is True for value in gates.values())


def _load_active_state(state_root: Path) -> Mapping[str, Any] | None:
    path = state_root / "active-lock.json"
    if not path.exists():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise AudisorLifecycleError("active lock state is malformed")
    return value


def verify_active_state(state: Mapping[str, Any]) -> tuple[bool, str, Mapping[str, Any] | None]:
    primary_lock, contract = state.get("primary_lock"), state.get("execution_contract")
    if not isinstance(primary_lock, Mapping) or not isinstance(contract, Mapping):
        return False, "active state lacks a primary lock and execution contract", None
    if not verify_lock(primary_lock):
        return False, "primary lock verification failed", None
    if not verify_contract(contract) or not _ready_contract(contract):
        return False, "execution contract verification failed", None
    bound = primary_lock.get("canonical_payload", {}).get("execution_contract_sha256") if isinstance(primary_lock.get("canonical_payload"), Mapping) else None
    actual = contract.get("lock_payload", {}).get("sha256") if isinstance(contract.get("lock_payload"), Mapping) else None
    if bound != actual:
        return False, "primary lock is not bound to the execution contract", None
    if state.get("drift_state") != "valid":
        return False, "contract drift state is not valid", None
    return True, "verified active execution state", contract


def _targets_authorized(targets: list[str], contract: Mapping[str, Any]) -> tuple[bool, str]:
    authority = contract.get("authority", {})
    allowed = authority.get("allowed_paths", []) if isinstance(authority, Mapping) else []
    prohibited = authority.get("prohibited_paths", []) if isinstance(authority, Mapping) else []
    actions = contract.get("implementation_plan", [])
    planned = [path for action in actions if isinstance(action, Mapping) for path in action.get("target_paths", []) if isinstance(path, str)] if isinstance(actions, list) else []

    # Use canonical path_security for allowed/prohibited checks
    ok, reason = check_paths_allowed(targets, allowed, prohibited)
    if not ok:
        return False, reason

    # Planned path check remains contract-specific
    for target in targets:
        if not any(
            PurePosixPath(target) == PurePosixPath(path) or PurePosixPath(path) in PurePosixPath(target).parents
            for path in planned
        ):
            return False, f"target is absent from the accepted action set: {target}"
    return True, "requested targets are authorized"


def _audit(state_root: Path, record: Mapping[str, Any]) -> Path:
    """Write an audit record. Raises HookAuditError on failure."""
    try:
        audit_dir = state_root / "audit"
        audit_dir.mkdir(parents=True, exist_ok=True)
        stamp = record["timestamp"].replace(":", "").replace("+00:00", "Z")
        digest = hashlib.sha256(canonical_text(record).encode("utf-8")).hexdigest()[:16]
        path = audit_dir / f"{stamp}-{digest}.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8", newline="\n")
        os.replace(temporary, path)
        return path
    except OSError as exc:
        raise HookAuditError(f"audit write failed: {exc}") from exc


def evaluate_hook_payload(payload: Mapping[str, Any], state_root: Path) -> dict[str, Any]:
    """Evaluate a hook payload and return a decision record.

    Raises:
        HookAuditError: If the audit record cannot be written.
    """
    event = str(payload.get("hook_event_name", "PreToolUse"))
    tool = str(payload.get("tool_name") or payload.get("tool") or "unknown")
    mutation = is_mutation_attempt(payload)
    decision, reason, targets, lock_present, lock_valid, authority_valid = "allow", "read-only operation", [], False, False, False
    try:
        if mutation:
            targets = requested_targets(payload) or []
            state = _load_active_state(state_root)
            lock_present = state is not None
            if not targets:
                decision, reason = "deny", "mutation targets are missing or ambiguous"
            elif state is None:
                decision, reason = "deny", "no active Audisor execution lock exists"
            else:
                lock_valid, reason, contract = verify_active_state(state)
                if not lock_valid:
                    decision = "deny"
                else:
                    authority_valid, reason = _targets_authorized(targets, contract)
                    decision = "allow" if authority_valid else "deny"
    except HookAuditError:
        raise
    except Exception as exc:
        decision, reason = "error", f"hook verification error: {type(exc).__name__}"
    host_permission_decision = None if decision == "allow" else "deny"
    process_exit_code = 0
    record = {"event": event, "timestamp": datetime.now(UTC).isoformat(), "hook_name": "audisor_pretool", "mutation_tool": tool, "requested_targets": targets, "lock_present": lock_present, "lock_valid": lock_valid, "authority_valid": authority_valid, "decision": decision, "internal_decision": decision, "host_permission_decision": host_permission_decision, "process_exit_code": process_exit_code, "reason": reason}
    audit_path = _audit(state_root, record)
    return {"decision": decision, "internal_decision": decision, "host_permission_decision": host_permission_decision, "process_exit_code": process_exit_code, "reason": reason, "audit_path": str(audit_path)}


# ---------------------------------------------------------------------------
# Codex PreToolUse protocol output
# ---------------------------------------------------------------------------


def _emit_deny(reason: str) -> None:
    """Write structured denial JSON to stdout. Raises HookOutputError on failure."""
    try:
        output = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        }
        print(json.dumps(output, ensure_ascii=False))
        sys.stdout.flush()
    except (OSError, ValueError, TypeError) as exc:
        raise HookOutputError(f"failed to emit denial: {exc}") from exc


def _parse_stdin() -> Mapping[str, Any]:
    """Parse and validate hook stdin. Raises HookInputError on failure."""
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError, OSError) as exc:
        raise HookInputError(f"cannot parse stdin: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise HookInputError("payload must be a JSON object")
    if not payload.get("tool_name"):
        raise HookInputError("missing tool_name")
    return payload


def main() -> int:
    """Entry point for the PreToolUse hook process.

    Exit codes:
        0 — allow (no stdout) or deny (structured JSON on stdout).
        2 — audit or output failure (reason on stderr).
    """
    # Phase 1: Parse input (fail-closed)
    try:
        payload = _parse_stdin()
    except HookInputError as exc:
        try:
            _emit_deny(f"malformed hook input: {exc}")
        except HookOutputError as out_exc:
            print(str(out_exc), file=sys.stderr)
            return 2
        return 0

    # Phase 2: Resolve state root
    state = Path(os.environ.get("AUDISOR_STATE_ROOT") or os.environ.get("AFLOW_STATE_ROOT") or default_state_root())

    # Phase 3: Evaluate
    try:
        result = evaluate_hook_payload(payload, state)
    except HookAuditError as exc:
        print(f"audit failure: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        # Unexpected internal error — deny fail-closed
        try:
            _emit_deny(f"hook internal error: {type(exc).__name__}: {exc}")
        except HookOutputError as out_exc:
            print(str(out_exc), file=sys.stderr)
            return 2
        return 0

    # Phase 4: Emit decision
    if result["decision"] != "allow":
        try:
            _emit_deny(result["reason"])
        except HookOutputError as exc:
            print(str(exc), file=sys.stderr)
            return 2
    # Allow: exit 0, no stdout
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
