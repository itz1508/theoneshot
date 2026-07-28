"""Behavioural tests for scripts/check_size.py (docs/code-size-policy.md).

Each test builds a sandbox repository under tmp_path and drives the
checker through its CLI entry point so exit codes and report lines are
proven end to end. Mirrors the mandatory size-policy fixture cases:
new violation, oversized function, baseline ratchet (unchanged /
increased / reduced), exemptions, exceptions (valid / exceeded /
expired / stale), stale baseline, rename evasion, malformed
configuration, and determinism.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from check_size import main  # noqa: E402


def _write(path: Path, lines: int, body: str = "x = 1") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(body for _ in range(lines)) + "\n", encoding="utf-8")


def _write_function(path: Path, name: str, span: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join("    x = 1" for _ in range(span - 1))
    path.write_text(f"def {name}():\n{body}\n", encoding="utf-8")


def _config(root: Path, baseline: list[dict] | None = None,
            exempt: list[dict] | None = None,
            exceptions: list[dict] | None = None) -> tuple[Path, Path]:
    baseline_path = root / "size_baseline.json"
    exceptions_path = root / "size_exceptions.json"
    baseline_path.write_text(
        json.dumps({"version": 1, "entries": baseline or []}), encoding="utf-8")
    exceptions_path.write_text(
        json.dumps({"version": 1, "exempt": exempt or [],
                    "exceptions": exceptions or []}), encoding="utf-8")
    return baseline_path, exceptions_path


def _run(root: Path, baseline_path: Path, exceptions_path: Path,
         capsys, extra: list[str] | None = None) -> tuple[int, str]:
    argv = ["--repo-root", str(root), "--baseline", str(baseline_path),
            "--exceptions", str(exceptions_path), "--scan-root", "pkg",
            "--today", "2026-07-27"] + (extra or [])
    code = main(argv)
    return code, capsys.readouterr().out


class TestNewViolations:
    def test_new_oversized_module_fails(self, tmp_path, capsys):
        _write(tmp_path / "pkg" / "big.py", 450)
        code, out = _run(tmp_path, *_config(tmp_path), capsys)
        assert code == 1
        assert "FAIL new-violation pkg/big.py physical_lines=450" in out

    def test_new_oversized_function_fails(self, tmp_path, capsys):
        _write_function(tmp_path / "pkg" / "mod.py", "huge", 120)
        code, out = _run(tmp_path, *_config(tmp_path), capsys)
        assert code == 1
        assert "FAIL new-violation pkg/mod.py::huge symbol_span=120" in out

    def test_compliant_module_passes(self, tmp_path, capsys):
        _write(tmp_path / "pkg" / "small.py", 100)
        code, out = _run(tmp_path, *_config(tmp_path), capsys)
        assert code == 0
        assert "PASS" in out

    def test_oversized_test_module_fails(self, tmp_path, capsys):
        _write(tmp_path / "pkg" / "tests" / "test_big.py", 600)
        code, out = _run(tmp_path, *_config(tmp_path), capsys)
        assert code == 1
        assert "FAIL new-violation pkg/tests/test_big.py physical_lines=600" in out


class TestBaselineRatchet:
    BASELINE = [{"path": "pkg/big.py", "symbol": None,
                 "metric": "physical_lines", "value": 450}]

    def test_unchanged_baseline_warns_but_passes(self, tmp_path, capsys):
        _write(tmp_path / "pkg" / "big.py", 450)
        code, out = _run(tmp_path, *_config(tmp_path, baseline=self.BASELINE), capsys)
        assert code == 0
        assert "WARN unchanged-baseline pkg/big.py" in out

    def test_increased_baseline_fails(self, tmp_path, capsys):
        _write(tmp_path / "pkg" / "big.py", 460)
        code, out = _run(tmp_path, *_config(tmp_path, baseline=self.BASELINE), capsys)
        assert code == 1
        assert "FAIL increased-baseline pkg/big.py physical_lines=460" in out

    def test_reduced_baseline_passes_with_update_hint(self, tmp_path, capsys):
        _write(tmp_path / "pkg" / "big.py", 430)
        code, out = _run(tmp_path, *_config(tmp_path, baseline=self.BASELINE), capsys)
        assert code == 0
        assert "OK reduced-baseline pkg/big.py" in out
        assert "--update-baseline" in out

    def test_stale_baseline_entry_fails(self, tmp_path, capsys):
        _write(tmp_path / "pkg" / "big.py", 100)  # now compliant
        code, out = _run(tmp_path, *_config(tmp_path, baseline=self.BASELINE), capsys)
        assert code == 1
        assert "FAIL stale-baseline pkg/big.py" in out

    def test_rename_does_not_inherit_baseline(self, tmp_path, capsys):
        _write(tmp_path / "pkg" / "renamed.py", 450)  # big.py renamed
        code, out = _run(tmp_path, *_config(tmp_path, baseline=self.BASELINE), capsys)
        assert code == 1
        assert "FAIL new-violation pkg/renamed.py" in out
        assert "FAIL stale-baseline pkg/big.py" in out


class TestExemptAndExceptions:
    def test_exempt_file_never_fails(self, tmp_path, capsys):
        _write(tmp_path / "pkg" / "schema_data.py", 700)
        exempt = [{"path": "pkg/schema_data.py", "category": "declarative schemas"}]
        code, out = _run(tmp_path, *_config(tmp_path, exempt=exempt), capsys)
        assert code == 0
        assert 'EXEMPT pkg/schema_data.py category="declarative schemas"' in out

    def test_valid_exception_within_limit_passes(self, tmp_path, capsys):
        _write(tmp_path / "pkg" / "big.py", 450)
        exceptions = [self._exception(limit=460, expires="2026-12-31")]
        code, out = _run(tmp_path, *_config(tmp_path, exceptions=exceptions), capsys)
        assert code == 0
        assert "OK exception pkg/big.py" in out

    def test_exception_exceeded_fails(self, tmp_path, capsys):
        _write(tmp_path / "pkg" / "big.py", 480)
        exceptions = [self._exception(limit=460, expires="2026-12-31")]
        code, out = _run(tmp_path, *_config(tmp_path, exceptions=exceptions), capsys)
        assert code == 1
        assert "FAIL exception-exceeded pkg/big.py" in out

    def test_expired_temporary_exception_fails(self, tmp_path, capsys):
        _write(tmp_path / "pkg" / "big.py", 450)
        exceptions = [self._exception(limit=460, expires="2026-01-01")]
        code, out = _run(tmp_path, *_config(tmp_path, exceptions=exceptions), capsys)
        assert code == 1
        assert "FAIL expired-exception pkg/big.py" in out
        assert "expired=2026-01-01" in out

    def test_stale_exception_fails(self, tmp_path, capsys):
        _write(tmp_path / "pkg" / "big.py", 100)  # compliant, exception unmatched
        exceptions = [self._exception(limit=460, expires="2026-12-31")]
        code, out = _run(tmp_path, *_config(tmp_path, exceptions=exceptions), capsys)
        assert code == 1
        assert "FAIL stale-exception pkg/big.py" in out

    @staticmethod
    def _exception(limit: int, expires: str) -> dict:
        return {"path": "pkg/big.py", "symbol": None, "metric": "physical_lines",
                "limit": limit, "reason": "test", "scope": "temporary",
                "owner": "runtime", "validation": "pytest", "expires": expires}


class TestMalformedConfiguration:
    def test_invalid_baseline_json_exits_2(self, tmp_path, capsys):
        _write(tmp_path / "pkg" / "small.py", 10)
        baseline_path, exceptions_path = _config(tmp_path)
        baseline_path.write_text("{not json", encoding="utf-8")
        code, out = _run(tmp_path, baseline_path, exceptions_path, capsys)
        assert code == 2
        assert "CONFIG-ERROR" in out

    def test_wrong_baseline_version_exits_2(self, tmp_path, capsys):
        baseline_path, exceptions_path = _config(tmp_path)
        baseline_path.write_text(json.dumps({"version": 2, "entries": []}),
                                 encoding="utf-8")
        code, out = _run(tmp_path, baseline_path, exceptions_path, capsys)
        assert code == 2

    def test_temporary_exception_without_expiry_exits_2(self, tmp_path, capsys):
        baseline_path, exceptions_path = _config(tmp_path)
        entry = {"path": "pkg/big.py", "symbol": None, "metric": "physical_lines",
                 "limit": 460, "reason": "test", "scope": "temporary",
                 "owner": "runtime", "validation": "pytest"}
        exceptions_path.write_text(
            json.dumps({"version": 1, "exempt": [], "exceptions": [entry]}),
            encoding="utf-8")
        code, out = _run(tmp_path, baseline_path, exceptions_path, capsys)
        assert code == 2

    def test_invalid_exempt_category_exits_2(self, tmp_path, capsys):
        baseline_path, exceptions_path = _config(
            tmp_path, exempt=[{"path": "pkg/x.py", "category": "just big"}])
        code, out = _run(tmp_path, baseline_path, exceptions_path, capsys)
        assert code == 2


class TestBaselineUpdate:
    def test_update_baseline_bootstraps_missing_file(self, tmp_path, capsys):
        _write(tmp_path / "pkg" / "big.py", 450)
        _, exceptions_path = _config(tmp_path)
        baseline_path = tmp_path / "new_baseline.json"
        code, out = _run(tmp_path, baseline_path, exceptions_path, capsys,
                         extra=["--update-baseline"])
        assert code == 0
        data = json.loads(baseline_path.read_text(encoding="utf-8"))
        assert data["version"] == 1
        assert data["entries"] == [{"path": "pkg/big.py", "symbol": None,
                                    "metric": "physical_lines", "value": 450}]

    def test_update_baseline_excludes_exempt_paths(self, tmp_path, capsys):
        _write(tmp_path / "pkg" / "big.py", 450)
        _write(tmp_path / "pkg" / "data.py", 700)
        exempt = [{"path": "pkg/data.py", "category": "static data"}]
        baseline_path, exceptions_path = _config(tmp_path, exempt=exempt)
        code, _ = _run(tmp_path, baseline_path, exceptions_path, capsys,
                       extra=["--update-baseline"])
        assert code == 0
        data = json.loads(baseline_path.read_text(encoding="utf-8"))
        assert [e["path"] for e in data["entries"]] == ["pkg/big.py"]


class TestDeterminism:
    def test_repeated_runs_produce_identical_output(self, tmp_path, capsys):
        _write(tmp_path / "pkg" / "big.py", 450)
        _write_function(tmp_path / "pkg" / "mod.py", "huge", 120)
        paths = _config(tmp_path)
        _, first = _run(tmp_path, *paths, capsys)
        _, second = _run(tmp_path, *paths, capsys)
        assert first == second

    def test_excluded_directories_are_skipped(self, tmp_path, capsys):
        _write(tmp_path / "pkg" / ".venv" / "vendor.py", 900)
        _write(tmp_path / "pkg" / "__pycache__" / "cached.py", 900)
        code, out = _run(tmp_path, *_config(tmp_path), capsys)
        assert code == 0
        assert "vendor.py" not in out
        assert "cached.py" not in out


class TestAudisorBackendDiscovery:
    """Prove audisor/backend is discovered by the size checker."""

    def test_audisor_backend_violations_discovered(self, tmp_path, capsys):
        """Violations under audisor/backend are discovered."""
        # Create a file that exceeds the hard limit
        _write(tmp_path / "audisor" / "backend" / "src" / "oversized.py", 600)
        paths = _config(tmp_path)
        code, out = _run(tmp_path, *paths, capsys, extra=["--scan-root", "audisor/backend"])
        assert "oversized.py" in out
        assert "physical_lines=600" in out

    def test_audisor_backend_excluded_parts_respected(self, tmp_path, capsys):
        """Excluded parts under audisor/backend are skipped."""
        _write(tmp_path / "audisor" / "backend" / ".venv" / "vendor.py", 900)
        _write(tmp_path / "audisor" / "backend" / "__pycache__" / "cached.py", 900)
        paths = _config(tmp_path)
        code, out = _run(tmp_path, *paths, capsys, extra=["--scan-root", "audisor/backend"])
        assert code == 0
        assert "vendor.py" not in out
        assert "cached.py" not in out
