"""Wire-format tests for the PreToolUse hook protocol.

These tests invoke ``main()`` through a real subprocess to verify the exact
Codex PreToolUse protocol:
  - Allow: exit 0, empty stdout.
  - Deny:  exit 0, JSON with permissionDecision on stdout.
  - Audit/output failure: exit 2, reason on stderr.
  - Exit 1 is never used.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures" / "hook_wire"
HOOK_MODULE = "audisor.audisor_lifecycle.hook"


def run_hook(stdin_data: str | bytes, state_root: Path | None = None) -> subprocess.CompletedProcess[str]:
    """Run the hook as a subprocess with the given stdin."""
    env = {"PATH": "", "SYSTEMROOT": ""}
    import os
    env = dict(os.environ)
    if state_root is not None:
        env["AUDISOR_STATE_ROOT"] = str(state_root)
    elif "AUDISOR_STATE_ROOT" in env:
        del env["AUDISOR_STATE_ROOT"]
    if "AFLOW_STATE_ROOT" in env:
        del env["AFLOW_STATE_ROOT"]
    if isinstance(stdin_data, bytes):
        return subprocess.run(
            [sys.executable, "-m", HOOK_MODULE],
            input=stdin_data,
            capture_output=True,
            env=env,
            cwd=str(Path(__file__).resolve().parents[3]),
        )
    return subprocess.run(
        [sys.executable, "-m", HOOK_MODULE],
        input=stdin_data,
        capture_output=True,
        text=True,
        env=env,
        cwd=str(Path(__file__).resolve().parents[3]),
    )


def fixture_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def fixture_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Malformed input tests (fail-closed → deny, exit 0)
# ---------------------------------------------------------------------------


class TestMalformedInput:
    def test_invalid_json_denied(self, tmp_path: Path) -> None:
        result = run_hook(fixture_text("malformed_not_json.txt"), tmp_path)
        assert result.returncode == 0
        output = json.loads(result.stdout)
        assert output["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert "malformed hook input" in output["hookSpecificOutput"]["permissionDecisionReason"]

    def test_json_array_denied(self, tmp_path: Path) -> None:
        result = run_hook(fixture_text("malformed_array.json"), tmp_path)
        assert result.returncode == 0
        output = json.loads(result.stdout)
        assert output["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert "malformed hook input" in output["hookSpecificOutput"]["permissionDecisionReason"]

    def test_missing_tool_name_denied(self, tmp_path: Path) -> None:
        result = run_hook(fixture_text("missing_tool_name.json"), tmp_path)
        assert result.returncode == 0
        output = json.loads(result.stdout)
        assert output["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert "missing tool_name" in output["hookSpecificOutput"]["permissionDecisionReason"]

    def test_empty_stdin_denied(self, tmp_path: Path) -> None:
        result = run_hook("", tmp_path)
        assert result.returncode == 0
        output = json.loads(result.stdout)
        assert output["hookSpecificOutput"]["permissionDecision"] == "deny"


# ---------------------------------------------------------------------------
# Read-only operation tests (allow, exit 0, no stdout)
# ---------------------------------------------------------------------------


class TestReadOnly:
    def test_readonly_bash_allowed(self, tmp_path: Path) -> None:
        result = run_hook(fixture_text("bash_readonly.json"), tmp_path)
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_mcp_tool_allowed(self, tmp_path: Path) -> None:
        """MCP tool calls are not mutations — allowed without lock."""
        result = run_hook(fixture_text("mcp_tool.json"), tmp_path)
        assert result.returncode == 0
        assert result.stdout.strip() == ""


# ---------------------------------------------------------------------------
# Mutation tests (deny without lock, exit 0)
# ---------------------------------------------------------------------------


class TestMutationDenial:
    def test_bash_mutation_denied_without_lock(self, tmp_path: Path) -> None:
        result = run_hook(fixture_text("bash_mutation.json"), tmp_path)
        assert result.returncode == 0
        output = json.loads(result.stdout)
        assert output["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert "no active Audisor execution lock" in output["hookSpecificOutput"]["permissionDecisionReason"]

    def test_apply_patch_mutation_denied_without_lock(self, tmp_path: Path) -> None:
        result = run_hook(fixture_text("apply_patch_mutation.json"), tmp_path)
        assert result.returncode == 0
        output = json.loads(result.stdout)
        assert output["hookSpecificOutput"]["permissionDecision"] == "deny"


# ---------------------------------------------------------------------------
# Audit failure tests (exit 2, stderr)
# ---------------------------------------------------------------------------


class TestAuditFailure:
    def test_audit_directory_permission_failure(self, tmp_path: Path) -> None:
        """If the audit directory cannot be created, exit 2 with stderr."""
        # Create a file where the audit directory should be
        state = tmp_path / "state"
        state.mkdir()
        (state / "audit").write_text("blocker", encoding="utf-8")
        result = run_hook(fixture_text("bash_mutation.json"), state)
        assert result.returncode == 2
        assert "audit" in result.stderr.lower()
        assert result.stdout.strip() == ""


# ---------------------------------------------------------------------------
# Protocol compliance
# ---------------------------------------------------------------------------


class TestProtocolCompliance:
    def test_exit_1_never_used_on_deny(self, tmp_path: Path) -> None:
        """Exit code 1 is never used for any hook outcome."""
        result = run_hook(fixture_text("bash_mutation.json"), tmp_path)
        assert result.returncode != 1

    def test_exit_1_never_used_on_malformed(self, tmp_path: Path) -> None:
        result = run_hook(fixture_text("malformed_not_json.txt"), tmp_path)
        assert result.returncode != 1

    def test_deny_uses_permission_decision_not_system_message(self, tmp_path: Path) -> None:
        """Denial output uses permissionDecision, not bare systemMessage."""
        result = run_hook(fixture_text("bash_mutation.json"), tmp_path)
        output = json.loads(result.stdout)
        hook_out = output["hookSpecificOutput"]
        assert "permissionDecision" in hook_out
        assert hook_out["hookEventName"] == "PreToolUse"
        assert "permissionDecisionReason" in hook_out

    def test_allow_produces_no_stdout(self, tmp_path: Path) -> None:
        result = run_hook(fixture_text("bash_readonly.json"), tmp_path)
        assert result.returncode == 0
        assert result.stdout == ""


# ---------------------------------------------------------------------------
# Wire-format target extraction
# ---------------------------------------------------------------------------


class TestTargetExtraction:
    def test_bash_command_targets_extracted(self, tmp_path: Path) -> None:
        """Bash tool_input.command is parsed for targets."""
        from audisor.audisor_lifecycle.hook import requested_targets
        payload = json.loads(fixture_text("bash_mutation.json"))
        targets = requested_targets(payload)
        assert targets == ["openai_project/runtime/src/audisor/audisor_lifecycle/hook.py"]

    def test_apply_patch_command_targets_extracted(self, tmp_path: Path) -> None:
        """apply_patch tool_input.command is parsed for patch targets."""
        from audisor.audisor_lifecycle.hook import requested_targets
        payload = json.loads(fixture_text("apply_patch_mutation.json"))
        targets = requested_targets(payload)
        assert targets == ["openai_project/runtime/src/audisor/audisor_lifecycle/hook.py"]

    def test_apply_patch_absolute_windows_target_is_repository_relative(self) -> None:
        from audisor.audisor_lifecycle.hook import requested_targets
        payload = {
            "tool_name": "apply_patch",
            "tool_input": {
                "command": "*** Begin Patch\n*** Update File: D:/Dev/Theoneshot/.codex/audisor-state/live-hook-denial-proof.txt\n@@\n-original-content\n+mutated-content\n*** End Patch"
            },
        }
        assert requested_targets(payload) == [".codex/audisor-state/live-hook-denial-proof.txt"]

    def test_readonly_bash_no_targets(self, tmp_path: Path) -> None:
        """Read-only bash commands produce no targets (not a mutation)."""
        from audisor.audisor_lifecycle.hook import requested_targets, is_mutation_attempt
        payload = json.loads(fixture_text("bash_readonly.json"))
        assert not is_mutation_attempt(payload)
