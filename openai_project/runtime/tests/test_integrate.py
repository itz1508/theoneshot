"""Tests for the Codex integration installer."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from audisor.integrate import (
    MANAGED_BEGIN,
    MANAGED_END,
    DriftError,
    IntegrateError,
    ManagedBlockError,
    TomlValidationError,
    UnmanagedConflictError,
    _find_managed_block,
    _generate_managed_block,
    apply,
    dry_run,
    remove,
    status,
)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """Create a minimal repository structure."""
    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir()
    runtime_dir = tmp_path / "openai_project" / "runtime"
    runtime_dir.mkdir(parents=True)
    config = codex_dir / "config.toml"
    config.write_text(
        "# Workspace-level Codex configuration.\n"
        "\n"
        "[features]\n"
        "hooks = true\n",
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture()
def repo_with_block(repo: Path) -> Path:
    """Repository with an existing managed block."""
    config = repo / ".codex" / "config.toml"
    block = _generate_managed_block(repo)
    text = config.read_text(encoding="utf-8").rstrip("\n") + "\n\n" + block
    config.write_text(text, encoding="utf-8")
    return repo


def _mock_health_check(repo_root: Path) -> dict:
    """Always-OK health check for testing."""
    return {"ok": True, "detail": "mocked"}


# ---------------------------------------------------------------------------
# TOML and block detection
# ---------------------------------------------------------------------------


class TestBlockDetection:
    def test_no_block(self, repo: Path) -> None:
        text = (repo / ".codex/config.toml").read_text(encoding="utf-8")
        assert _find_managed_block(text) is None

    def test_find_block(self, repo_with_block: Path) -> None:
        text = (repo_with_block / ".codex/config.toml").read_text(encoding="utf-8")
        block = _find_managed_block(text)
        assert block is not None
        begin, end = block
        assert begin >= 0
        assert end > begin

    def test_duplicate_markers_fail(self, tmp_path: Path) -> None:
        text = f"{MANAGED_BEGIN}\n{MANAGED_END}\n{MANAGED_BEGIN}\n{MANAGED_END}\n"
        with pytest.raises(ManagedBlockError, match="duplicate"):
            _find_managed_block(text)

    def test_out_of_order_markers_fail(self, tmp_path: Path) -> None:
        text = f"{MANAGED_END}\n{MANAGED_BEGIN}\n"
        with pytest.raises(ManagedBlockError, match="out of order"):
            _find_managed_block(text)

    def test_malformed_toml_fails(self, repo: Path) -> None:
        (repo / ".codex/config.toml").write_text("[invalid toml {{", encoding="utf-8")
        with pytest.raises(TomlValidationError):
            dry_run(repo)


# ---------------------------------------------------------------------------
# Dry-run
# ---------------------------------------------------------------------------


class TestDryRun:
    def test_no_block_reports_insert(self, repo: Path) -> None:
        result = dry_run(repo)
        assert result["managed_block_present"] is False
        assert result["action"] == "insert managed block"
        assert result["filesystem_changes"] == 1

    def test_existing_current_block_no_change(self, repo_with_block: Path) -> None:
        result = dry_run(repo_with_block)
        assert result["managed_block_present"] is True
        assert result["action"] == "no change (block is current)"

    def test_unmanaged_aflow_conflict(self, repo: Path) -> None:
        config = repo / ".codex/config.toml"
        config.write_text(
            '[mcp_servers.aflow]\ncommand = "other"\n',
            encoding="utf-8",
        )
        with pytest.raises(UnmanagedConflictError):
            dry_run(repo)


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------


class TestApply:
    @patch("audisor.integrate._health_check", side_effect=_mock_health_check)
    def test_first_apply(self, mock_health, repo: Path) -> None:
        result = apply(repo)
        assert result["status"] == "applied"
        text = (repo / ".codex/config.toml").read_text(encoding="utf-8")
        assert MANAGED_BEGIN in text
        assert MANAGED_END in text

    @patch("audisor.integrate._health_check", side_effect=_mock_health_check)
    def test_second_apply_idempotent(self, mock_health, repo: Path) -> None:
        apply(repo)
        result = apply(repo)
        assert result["status"] == "no-change"

    @patch("audisor.integrate._health_check", side_effect=_mock_health_check)
    def test_apply_fails_on_health_check(self, mock_health, repo: Path) -> None:
        mock_health.side_effect = None
        mock_health.return_value = {"ok": False, "detail": "server failed"}
        with pytest.raises(IntegrateError, match="health check"):
            apply(repo)

    @patch("audisor.integrate._health_check", side_effect=_mock_health_check)
    def test_apply_drift_blocks_without_repair(self, mock_health, repo_with_block: Path) -> None:
        config = repo_with_block / ".codex/config.toml"
        text = config.read_text(encoding="utf-8")
        config.write_text(text.replace("required = true", "required = false"), encoding="utf-8")
        with pytest.raises(DriftError):
            apply(repo_with_block)

    @patch("audisor.integrate._health_check", side_effect=_mock_health_check)
    def test_apply_drift_with_repair(self, mock_health, repo_with_block: Path) -> None:
        config = repo_with_block / ".codex/config.toml"
        text = config.read_text(encoding="utf-8")
        config.write_text(text.replace("required = true", "required = false"), encoding="utf-8")
        result = apply(repo_with_block, repair=True)
        assert result["status"] == "applied"

    @patch("audisor.integrate._health_check", side_effect=_mock_health_check)
    def test_apply_preserves_unrelated_config(self, mock_health, repo: Path) -> None:
        config = repo / ".codex/config.toml"
        original = config.read_text(encoding="utf-8")
        apply(repo)
        result_text = config.read_text(encoding="utf-8")
        # Original content should be preserved
        assert "[features]" in result_text
        assert "hooks = true" in result_text

    @patch("audisor.integrate._health_check", side_effect=_mock_health_check)
    def test_apply_rollback_on_write_failure(self, mock_health, repo: Path) -> None:
        """If the write itself fails, rollback to snapshot."""
        config = repo / ".codex/config.toml"
        snapshot = config.read_text(encoding="utf-8")

        from audisor import integrate

        call_count = {"n": 0}
        original_write = integrate._write_config

        def failing_write(root, text):
            call_count["n"] += 1
            if call_count["n"] == 1:
                # First call (writing new config) — raise error
                raise OSError("simulated write failure")
            # Second call (rollback) — write the snapshot
            original_write(root, text)

        with patch.object(integrate, "_write_config", side_effect=failing_write):
            with pytest.raises(OSError, match="simulated"):
                apply(repo)
        # Config should be restored to snapshot
        assert config.read_text(encoding="utf-8") == snapshot


# ---------------------------------------------------------------------------
# Remove
# ---------------------------------------------------------------------------


class TestRemove:
    def test_remove_no_block(self, repo: Path) -> None:
        result = remove(repo)
        assert result["status"] == "no-change"

    @patch("audisor.integrate._health_check", side_effect=_mock_health_check)
    def test_remove_restores_config(self, mock_health, repo: Path) -> None:
        config = repo / ".codex/config.toml"
        original = config.read_text(encoding="utf-8")
        apply(repo)
        assert MANAGED_BEGIN in config.read_text(encoding="utf-8")
        result = remove(repo)
        assert result["status"] == "removed"
        assert MANAGED_BEGIN not in config.read_text(encoding="utf-8")
        assert "[features]" in config.read_text(encoding="utf-8")

    @patch("audisor.integrate._health_check", side_effect=_mock_health_check)
    def test_remove_drifted_requires_force(self, mock_health, repo: Path) -> None:
        apply(repo)
        config = repo / ".codex/config.toml"
        text = config.read_text(encoding="utf-8")
        config.write_text(text.replace("required = true", "required = false"), encoding="utf-8")
        with pytest.raises(DriftError):
            remove(repo)

    @patch("audisor.integrate._health_check", side_effect=_mock_health_check)
    def test_remove_drifted_with_force(self, mock_health, repo: Path) -> None:
        apply(repo)
        config = repo / ".codex/config.toml"
        text = config.read_text(encoding="utf-8")
        config.write_text(text.replace("required = true", "required = false"), encoding="utf-8")
        result = remove(repo, force=True)
        assert result["status"] == "removed"


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


class TestStatus:
    def test_status_no_block(self, repo: Path) -> None:
        result = status(repo)
        assert result["config_valid"] is True
        assert result["managed_block_present"] is False

    @patch("audisor.integrate._health_check", side_effect=_mock_health_check)
    def test_status_with_block(self, mock_health, repo: Path) -> None:
        apply(repo)
        result = status(repo)
        assert result["managed_block_present"] is True
        assert result["block_current"] is True
        assert result["server_command"] == "uv"


# ---------------------------------------------------------------------------
# Generated block content
# ---------------------------------------------------------------------------


class TestGeneratedBlock:
    def test_block_contains_absolute_paths(self, repo: Path) -> None:
        block = _generate_managed_block(repo)
        assert str(repo.resolve()) in block.replace("\\\\", "\\")
        assert "no-sync" in block
        assert "offline" in block
        assert "required = true" in block

    def test_block_contains_state_root(self, repo: Path) -> None:
        block = _generate_managed_block(repo)
        assert "AUDISOR_STATE_ROOT" in block
        assert "audisor-state" in block

    def test_block_is_valid_toml(self, repo: Path) -> None:
        import tomllib
        block = _generate_managed_block(repo)
        # Strip markers
        inner = block.replace(MANAGED_BEGIN, "").replace(MANAGED_END, "")
        parsed = tomllib.loads(inner)
        assert "mcp_servers" in parsed
        assert "aflow" in parsed["mcp_servers"]
