"""Codex integration installer for the A-Flow MCP server.

Manages a single machine-local managed block in ``.codex/config.toml``
containing the ``[mcp_servers.aflow]`` table with absolute paths resolved
at install time.

Public interface (via ``audisor integrate``)::

    audisor integrate codex --scope repo --dry-run
    audisor integrate codex --scope repo --apply
    audisor integrate codex --scope repo --status
    audisor integrate codex --scope repo --remove [--force]

Source migrations (committed once, portable) are NOT performed here:
  - ``[agents.aflow]`` removal
  - ``.codex/aflow-state`` → ``.codex/audisor-state`` rename
  - ``--no-sync`` addition to ``.codex/hooks.json``
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MANAGED_BEGIN = "# BEGIN AUDISOR MANAGED: codex-aflow"
MANAGED_END = "# END AUDISOR MANAGED: codex-aflow"
CONFIG_RELATIVE = ".codex/config.toml"
HOOKS_RELATIVE = ".codex/hooks.json"
RUNTIME_RELATIVE = "openai_project/runtime"
MCP_MODULE = "audisor.aflow_mcp_server"


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class IntegrateError(Exception):
    """Base error for integration operations."""


class TomlValidationError(IntegrateError):
    """Existing TOML is malformed."""


class ManagedBlockError(IntegrateError):
    """Managed block is in an invalid state."""


class DriftError(IntegrateError):
    """Managed block content has drifted from expected."""


class UnmanagedConflictError(IntegrateError):
    """An unmanaged [mcp_servers.aflow] exists outside the managed block."""


# ---------------------------------------------------------------------------
# TOML helpers
# ---------------------------------------------------------------------------


def _parse_toml(text: str) -> dict[str, Any]:
    """Parse TOML, raising TomlValidationError on malformed input."""
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise TomlValidationError(f"malformed TOML: {exc}") from exc


def _find_managed_block(text: str) -> tuple[int, int] | None:
    """Find the managed block boundaries. Returns (begin, end) line indices or None.

    Raises ManagedBlockError on duplicate or out-of-order markers.
    """
    lines = text.splitlines(keepends=True)
    begins = [i for i, line in enumerate(lines) if line.strip() == MANAGED_BEGIN]
    ends = [i for i, line in enumerate(lines) if line.strip() == MANAGED_END]
    if len(begins) > 1 or len(ends) > 1:
        raise ManagedBlockError("duplicate managed block markers")
    if len(begins) == 0 and len(ends) == 0:
        return None
    if len(begins) != len(ends):
        raise ManagedBlockError("mismatched managed block markers")
    if begins[0] >= ends[0]:
        raise ManagedBlockError("managed block markers are out of order")
    return (begins[0], ends[0])


def _check_unmanaged_aflow(text: str) -> None:
    """Fail if [mcp_servers.aflow] exists outside the managed block."""
    parsed = _parse_toml(text)
    block = _find_managed_block(text)
    if block is not None:
        # Remove managed block content before parsing
        lines = text.splitlines(keepends=True)
        before = "".join(lines[: block[0]])
        after = "".join(lines[block[1] + 1 :])
        without_block = before + after
        parsed_without = _parse_toml(without_block)
    else:
        parsed_without = parsed

    servers = parsed_without.get("mcp_servers", {})
    if isinstance(servers, dict) and "aflow" in servers:
        raise UnmanagedConflictError(
            "unmanaged [mcp_servers.aflow] found outside managed block; "
            "remove it manually before using the installer"
        )


# ---------------------------------------------------------------------------
# Managed block generation
# ---------------------------------------------------------------------------


def _generate_managed_block(repo_root: Path) -> str:
    """Generate the managed block content with absolute paths."""
    abs_runtime = (repo_root / RUNTIME_RELATIVE).resolve()
    abs_repo = repo_root.resolve()
    abs_state = abs_repo / ".codex" / "audisor-state"

    # Normalize path separators for TOML
    runtime_path = str(abs_runtime).replace("\\", "\\\\")
    repo_path = str(abs_repo).replace("\\", "\\\\")
    state_path = str(abs_state).replace("\\", "\\\\")

    return (
        f"{MANAGED_BEGIN}\n"
        f"[mcp_servers.aflow]\n"
        f'command = "uv"\n'
        f'args = ["run", "--no-sync", "--offline", "--directory", "{runtime_path}", "python", "-m", "{MCP_MODULE}"]\n'
        f'cwd = "{repo_path}"\n'
        f"required = true\n"
        f"\n"
        f"[mcp_servers.aflow.env]\n"
        f'AUDISOR_STATE_ROOT = "{state_path}"\n'
        f"{MANAGED_END}\n"
    )


# ---------------------------------------------------------------------------
# Config operations
# ---------------------------------------------------------------------------


def _read_config(repo_root: Path) -> str:
    config_path = repo_root / CONFIG_RELATIVE
    if not config_path.exists():
        raise IntegrateError(f"{CONFIG_RELATIVE} does not exist")
    return config_path.read_text(encoding="utf-8")


def _write_config(repo_root: Path, text: str) -> None:
    config_path = repo_root / CONFIG_RELATIVE
    config_path.write_text(text, encoding="utf-8", newline="\n")


# ---------------------------------------------------------------------------
# Public operations
# ---------------------------------------------------------------------------


def dry_run(repo_root: Path) -> dict[str, Any]:
    """Report what --apply would do without modifying anything."""
    text = _read_config(repo_root)
    _parse_toml(text)  # validate
    _check_unmanaged_aflow(text)

    block = _find_managed_block(text)
    expected = _generate_managed_block(repo_root)

    report: dict[str, Any] = {
        "mode": "dry-run",
        "config_path": str(repo_root / CONFIG_RELATIVE),
        "managed_block_present": block is not None,
        "filesystem_changes": 0,
    }

    if block is None:
        report["action"] = "insert managed block"
        report["filesystem_changes"] = 1
        report["hook_trust_note"] = "hook review may be required after source migration commit"
    else:
        current_block = "".join(text.splitlines(keepends=True)[block[0] : block[1] + 1])
        if current_block.strip() == expected.strip():
            report["action"] = "no change (block is current)"
        else:
            report["action"] = "drift detected"
            report["repair_required"] = True
            report["note"] = "use --apply with --repair to overwrite drifted block"

    return report


def apply(repo_root: Path, *, repair: bool = False) -> dict[str, Any]:
    """Apply the managed block to the repository config."""
    text = _read_config(repo_root)
    _parse_toml(text)  # validate
    _check_unmanaged_aflow(text)

    block = _find_managed_block(text)
    expected = _generate_managed_block(repo_root)
    snapshot = text  # byte-exact snapshot for rollback

    lines = text.splitlines(keepends=True)

    if block is not None:
        current_block = "".join(lines[block[0] : block[1] + 1])
        if current_block.strip() == expected.strip():
            return {
                "mode": "apply",
                "status": "no-change",
                "message": "managed block is already current",
            }
        if not repair:
            raise DriftError(
                "managed block has drifted; use --repair to overwrite. "
                "This is an explicit authorization required."
            )

    # Step 1: Direct MCP health check
    health = _health_check(repo_root)
    if not health["ok"]:
        raise IntegrateError(f"MCP health check failed: {health['detail']}")

    # Step 2: Write config
    try:
        if block is not None:
            # Replace existing block
            new_lines = lines[: block[0]] + [expected] + lines[block[1] + 1 :]
        else:
            # Append block
            new_text = text.rstrip("\n") + "\n\n" + expected
            new_lines = [new_text]

        new_text = "".join(new_lines) if block is not None else new_text
        # Validate resulting TOML
        _parse_toml(new_text)
        _write_config(repo_root, new_text)
    except Exception:
        # Rollback
        _write_config(repo_root, snapshot)
        raise

    return {
        "mode": "apply",
        "status": "applied",
        "message": "managed block written",
        "health_check": health,
        "hook_trust_note": "hook review via /hooks may be required",
    }


def status(repo_root: Path) -> dict[str, Any]:
    """Report current integration status."""
    report: dict[str, Any] = {"mode": "status"}

    # Config status
    try:
        text = _read_config(repo_root)
        _parse_toml(text)
        report["config_valid"] = True
    except IntegrateError as exc:
        report["config_valid"] = False
        report["config_error"] = str(exc)
        return report

    block = _find_managed_block(text)
    report["managed_block_present"] = block is not None

    if block is not None:
        expected = _generate_managed_block(repo_root)
        current_block = "".join(text.splitlines(keepends=True)[block[0] : block[1] + 1])
        report["block_current"] = current_block.strip() == expected.strip()

        # Parse managed block to extract server info
        block_text = current_block.replace(MANAGED_BEGIN, "").replace(MANAGED_END, "")
        try:
            block_toml = tomllib.loads(block_text)
            servers = block_toml.get("mcp_servers", {})
            aflow = servers.get("aflow", {})
            report["server_command"] = aflow.get("command", "")
            report["server_args"] = aflow.get("args", [])
            report["server_cwd"] = aflow.get("cwd", "")
            report["server_env"] = aflow.get("env", {})
        except tomllib.TOMLDecodeError:
            report["block_parse_error"] = True

    # Hook trust status
    hooks_path = repo_root / HOOKS_RELATIVE
    report["hooks_configured"] = hooks_path.exists()
    if hooks_path.exists():
        try:
            hooks_data = json.loads(hooks_path.read_text(encoding="utf-8"))
            hooks_list = hooks_data.get("hooks", {}).get("PreToolUse", [])
            for entry in hooks_list:
                for hook in entry.get("hooks", []):
                    cmd = hook.get("command", "")
                    if "audisor.audisor_lifecycle.hook" in cmd:
                        report["hook_command"] = cmd
                        report["hook_uses_no_sync"] = "--no-sync" in cmd
                        break
        except (json.JSONDecodeError, OSError):
            report["hooks_parse_error"] = True

    # Trust detection: check if global config has trusted hash for this hook
    report["trust_state"] = "unknown"
    report["trust_note"] = "check via /hooks in Codex CLI"

    return report


def remove(repo_root: Path, *, force: bool = False) -> dict[str, Any]:
    """Remove the managed block from the repository config."""
    text = _read_config(repo_root)
    _parse_toml(text)

    block = _find_managed_block(text)
    if block is None:
        return {"mode": "remove", "status": "no-change", "message": "no managed block found"}

    lines = text.splitlines(keepends=True)
    expected = _generate_managed_block(repo_root)
    current_block = "".join(lines[block[0] : block[1] + 1])

    if current_block.strip() != expected.strip() and not force:
        raise DriftError(
            "managed block has drifted from expected content; "
            "use --force to remove anyway"
        )

    # Remove block and clean up extra blank lines
    new_lines = lines[: block[0]] + lines[block[1] + 1 :]
    new_text = "".join(new_lines).rstrip("\n") + "\n"

    # Validate
    _parse_toml(new_text)
    _write_config(repo_root, new_text)

    return {
        "mode": "remove",
        "status": "removed",
        "message": "managed block removed",
    }


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


def _health_check(repo_root: Path) -> dict[str, Any]:
    """Start the MCP server directly and verify it initializes."""
    runtime_dir = repo_root / RUNTIME_RELATIVE
    cmd = [
        sys.executable,
        "-m",
        MCP_MODULE,
    ]
    try:
        # Just test that the module can be imported and the server created
        check_cmd = [
            sys.executable,
            "-c",
            "from audisor.aflow_mcp_server import create_server; s = create_server(); print('ok')",
        ]
        result = subprocess.run(
            check_cmd,
            capture_output=True,
            text=True,
            cwd=str(runtime_dir.resolve()),
            timeout=30,
        )
        if result.returncode == 0 and "ok" in result.stdout:
            return {"ok": True, "detail": "server created successfully"}
        return {"ok": False, "detail": result.stderr.strip() or "unknown error"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "detail": "health check timed out"}
    except OSError as exc:
        return {"ok": False, "detail": str(exc)}


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def run_integrate(argv: list[str]) -> int:
    """CLI entry point for the integrate subcommand."""
    import argparse

    parser = argparse.ArgumentParser(prog="audisor integrate")
    parser.add_argument("target", choices=["codex"])
    parser.add_argument("--scope", choices=["repo"], default="repo")
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--dry-run", action="store_true")
    actions.add_argument("--apply", action="store_true")
    actions.add_argument("--status", action="store_true")
    actions.add_argument("--remove", action="store_true")
    parser.add_argument("--force", action="store_true", help="Force remove even if drifted")
    parser.add_argument("--repair", action="store_true", help="Overwrite drifted managed block")
    parser.add_argument("--repo-root", type=Path, default=None, help="Repository root (default: auto-detect)")

    args = parser.parse_args(argv)

    # Resolve repo root
    repo_root = args.repo_root or _detect_repo_root()
    if repo_root is None:
        print("error: cannot detect repository root; use --repo-root", file=sys.stderr)
        return 1

    try:
        if args.dry_run:
            result = dry_run(repo_root)
        elif args.apply:
            result = apply(repo_root, repair=args.repair)
        elif args.status:
            result = status(repo_root)
        elif args.remove:
            result = remove(repo_root, force=args.force)
        else:
            parser.error("no action specified")
            return 1
    except IntegrateError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def _detect_repo_root() -> Path | None:
    """Detect the repository root via git."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return Path(result.stdout.strip())
    except (subprocess.TimeoutExpired, OSError):
        pass
    return None
