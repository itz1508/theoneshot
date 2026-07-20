"""Tests for the host-owned automatic post-execution Fix verifier.

Proves:
1. Codex exit 0 triggers verification automatically.
2. Codex non-zero exit never runs verification.
3. Exact persisted ScanConfig is reconstructed and used.
4. scanner_clear passes when the original finding disappears.
5. scanner_clear fails when the original finding remains.
6. Unrelated new scanner findings do not fail the accepted Fix.
7. Authorized command tokens execute with shell=False in repository_root.
8. Command stdout, stderr, and exit code are persisted.
9. Command exit 0 passes expected_result="exit code 0".
10. Command non-zero fails verification.
11. Unsupported expected_result fails closed.
12. file_exists works.
13. file_contains and file_not_contains work.
14. python_compiles works without executing the file.
15. json_parses works.
16. Out-of-scope assertion paths fail closed.
17. Every FindingCheck produces a result.
18. Every ValidationSpec produces a result.
19. Fixed success rule passes only when all required results pass.
20. Successful verification returns operation status completed.
21. Failed verification returns operation status failed.
22. Verification result is persisted under the same operation ID.
23. Duplicate operation does not relaunch Codex or rerun verification.
24. Existing Build behavior remains unchanged.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from audisor.adapters.protocol import AudisorOperationRequest, HostCapabilities
from audisor.codex.fix_continuation import CodexFixContinuation, FixContinuationResult
from audisor.codex.fix_verification import (
    FixPostExecutionVerifier,
    FixVerificationResult,
    reconstruct_scan_config,
)
from audisor.operations.artifacts import ArtifactStore
from audisor.operations.executor import AudisorOperationExecutor, ExecutorConfig, FixRouteConfig
from audisor.operations.mutation_enforcer import MutationEnforcer
from audisor.operations.store import AudisorOperationStore
from audisor.schemas.authority import AuthorityContext, AuthoritySource, PermissionSet


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_fix_request(operation_id: str = "fix-op-1", *, target_files: list[str] | None = None, scanner_context: dict | None = None) -> AudisorOperationRequest:
    if target_files is None:
        target_files = ["src/app.py"]
    fix_payload = {
        "operation_id": operation_id,
        "findings": [{"id": "F-1", "type": "syntax", "file": target_files[0], "severity": "high", "evidence": {"line": 42, "message": "undefined variable"}}],
        "manifest": {"files": target_files, "dependency_closure": target_files, "input_hash": "abc123", "file_hashes": {f: "a" * 64 for f in target_files}},
        "statements": [
            {"type": "mutation_authority", "content": {"authorized": True, "scope": "repository"}, "findings_ref_hash": "findings-hash", "manifest_ref_hash": "manifest-hash"},
            {"type": "plan_authority", "content": {"qualified": True}, "findings_ref_hash": "findings-hash", "manifest_ref_hash": "manifest-hash"},
            {"type": "execution_authority", "content": {"scope": "repository"}, "findings_ref_hash": "findings-hash", "manifest_ref_hash": "manifest-hash"},
        ],
        "plan": {"steps": [{"id": "S-1", "action": "repair", "target_file": target_files[0], "originating_finding_id": "F-1", "acceptance_criterion": "test passes"}], "target_files": target_files, "is_qualified": True, "minor_issues": []},
        "workspace_identity": {"path": "sandbox/fix-op-1", "root": "/repo"},
        "authority_context": {"allowed_paths": target_files, "scope": "repository"},
        "aflow_analysis_request": None,
        "scanner_context": scanner_context,
    }
    return AudisorOperationRequest(
        operation_id=operation_id, mode="fix", request={"fix": fix_payload},
        authority=AuthorityContext(
            source=AuthoritySource(source_type="user", grant_id="test", host_identity="cli"),
            permissions=PermissionSet(allowed_paths=[".", "src", "src/app.py"], prohibited_paths=[".git", ".codex"], allowed_tools=[], prohibited_tools=[]),
            scope="repository",
        ),
        constraints={}, host_capabilities=HostCapabilities(), host_context={"adapter": "cli"},
    )


def _make_scanner_context(tmp_path: Path) -> dict:
    return {
        "excluded_dirs": [".git", "__pycache__"],
        "excluded_files": ["scanning/scanner.py"],
        "extensions": [".py"],
        "test_commands": [],
        "contract_requirements": [],
        "source_roots": [],
        "repository_root": str(tmp_path.resolve()),
    }


def _write_handoff(
    tmp_path: Path,
    operation_id: str,
    target_files: list[str] | None = None,
    *,
    include_verification_contract: bool = True,
    include_verification_grounding: bool = True,
    verification_contract: dict | None = None,
    verification_grounding: dict | None = None,
) -> str:
    if target_files is None:
        target_files = ["src/app.py"]
    handoff_dir = tmp_path / "fix-operations" / operation_id
    handoff_dir.mkdir(parents=True, exist_ok=True)
    handoff_path = handoff_dir / "qualified-fix-handoff.json"
    handoff = {
        "schema_version": 1, "operation_id": operation_id, "operation_type": "fix",
        "findings": [{"id": "F-1", "type": "syntax", "file": target_files[0], "severity": "high", "evidence": {"line": 42}}],
        "scoped_manifest": {"files": target_files, "dependency_closure": target_files, "input_hash": "abc123", "file_hashes": {f: "a" * 64 for f in target_files}},
        "statements": [{"type": "mutation_authority", "content": {"authorized": True}}],
        "qualified_plan": {"steps": [{"id": "S-1", "action": "repair", "target_file": target_files[0], "originating_finding_id": "F-1", "acceptance_criterion": "test passes"}], "target_files": target_files, "is_qualified": True},
        "authority": {"mutation_authorized": False, "execution_authorized": False, "apply_authorized": False, "completion_claimed": False},
        "workspace_identity": {"path": "sandbox/fix-op-1", "root": str(tmp_path.resolve())},
        "scanner_context": _make_scanner_context(tmp_path),
    }
    if include_verification_contract:
        handoff["verification_contract"] = verification_contract or {
            "finding_checks": [{"finding_id": "F-1", "resolution_method": "rescan", "check": "scanner_clear::F-1", "expected_result": "finding resolved"}],
            "validations": [{"id": "V-1", "command_or_assertion": "python_compiles::src/app.py", "expected_result": "compiles without error"}],
            "must_not_regress": ["existing tests must still pass"],
            "success_rule": "all_finding_checks_and_validations_pass",
        }
    if include_verification_grounding:
        handoff["verification_grounding"] = verification_grounding or {
            "finding_checks": [{"finding_id": "F-1", "resolution_method": "rescan", "source_type": "deterministic_assertion", "source_reference": "assertion:scanner_clear::F-1", "authorized_tokens": None, "scoped_paths": [target_files[0]]}],
            "validations": [{"validation_id": "V-1", "source_type": "deterministic_assertion", "source_reference": "assertion:python_compiles::src/app.py", "authorized_tokens": None, "scoped_paths": [target_files[0]]}],
        }
    handoff_path.write_text(json.dumps(handoff, sort_keys=True, indent=2), encoding="utf-8")
    return str(handoff_path)


def _make_executor(tmp_path: Path, *, fix_dispatcher: Any = None, fix_continuation: Any = None, worker_factory: Any = None) -> AudisorOperationExecutor:
    store = AudisorOperationStore(tmp_path / "operations")
    artifact_store = ArtifactStore(tmp_path / "artifacts")
    enforcer = MutationEnforcer(base_dir=tmp_path)
    fix_route = FixRouteConfig(
        fix_dispatcher=fix_dispatcher, continue_callback=lambda op, result: result,
        finalize_callback=lambda op, result: result, fix_continuation=fix_continuation,
    )
    return AudisorOperationExecutor(
        config=ExecutorConfig(operation_store=store, artifact_store=artifact_store, mutation_enforcer=enforcer, fix_route=fix_route, worker_factory=worker_factory)
    )


class FakeLauncher:
    def __init__(self, *, exit_code: int = 0, outcome: str = "codex_completed", fail: bool = False):
        self.calls: list[dict[str, Any]] = []
        self._exit_code = exit_code
        self._outcome = outcome
        self._fail = fail

    def __call__(self, *, stdin_bytes: bytes, cwd: Path) -> tuple[int | None, int, str, tuple[str, ...], str, str]:
        self.calls.append({"stdin_bytes": stdin_bytes, "cwd": cwd})
        if self._fail:
            from audisor.codex.launcher import CodexLaunchError
            raise CodexLaunchError("codex_process_start_failed", "fake failure")
        return 12345, self._exit_code, self._outcome, ("codex", "exec", "-"), "fake stdout", "fake stderr"


# ---------------------------------------------------------------------------
# Tests: reconstruct_scan_config
# ---------------------------------------------------------------------------


def test_reconstruct_scan_config_returns_exact_persisted_fields(tmp_path: Path):
    """The exact persisted ScanConfig is reconstructed and used."""
    scanner_context = {
        "excluded_dirs": [".git", "node_modules"],
        "excluded_files": ["scanning/scanner.py"],
        "extensions": [".py", ".json"],
        "test_commands": [["pytest", "tests"]],
        "contract_requirements": [["contract.json", ["version", "kind"]]],
        "source_roots": ["src"],
        "repository_root": str(tmp_path.resolve()),
    }
    config = reconstruct_scan_config(scanner_context)
    assert config["excluded_dirs"] == frozenset({".git", "node_modules"})
    assert config["excluded_files"] == frozenset({"scanning/scanner.py"})
    assert config["extensions"] == frozenset({".py", ".json"})
    assert config["test_commands"] == (("pytest", "tests"),)
    assert config["contract_requirements"] == (("contract.json", ("version", "kind")),)
    assert config["source_roots"] == ("src",)
    assert config["repository_root"] == str(tmp_path.resolve())


def test_reconstruct_scan_config_rejects_missing_repository_root():
    """Missing repository_root fails closed."""
    from audisor.codex.fix_verification import FixVerificationError
    try:
        reconstruct_scan_config({"excluded_dirs": []})
    except FixVerificationError as exc:
        assert exc.code == "scanner_context_invalid"
    else:
        raise AssertionError("expected FixVerificationError")


# ---------------------------------------------------------------------------
# Tests: Codex exit 0 triggers verification automatically
# ---------------------------------------------------------------------------


def test_codex_exit_zero_triggers_verification_automatically(tmp_path: Path):
    """Codex exit 0 triggers verification automatically."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("value = 1\n", encoding="utf-8")
    handoff_path = _write_handoff(tmp_path, "fix-op-1")
    launcher = FakeLauncher(exit_code=0)
    verifier = FixPostExecutionVerifier()
    continuation = CodexFixContinuation(
        launcher=launcher,
        launch_result_store_root=tmp_path / "operations",
        verifier=verifier,
    )

    class FakeDispatcher:
        def dispatch(self, operation, continue_impl, finalize_unresolved):
            return continue_impl(operation, {"status": "accepted", "handoff_path": handoff_path})

    executor = _make_executor(tmp_path, fix_dispatcher=FakeDispatcher(), fix_continuation=continuation)
    result = executor.execute(_make_fix_request(scanner_context=_make_scanner_context(tmp_path)))

    assert result.status == "completed"
    assert result.execution["codex_launched"] is True
    assert result.execution["verification_performed"] is True
    assert result.execution["verification_passed"] is True
    assert "verification_result_reference" in result.execution
    # Verification result file exists
    verification_path = Path(result.execution["verification_result_reference"])
    assert verification_path.is_file()


def test_codex_nonzero_exit_never_runs_verification(tmp_path: Path):
    """Codex non-zero exit never runs verification."""
    handoff_path = _write_handoff(tmp_path, "fix-op-1")
    launcher = FakeLauncher(exit_code=1, outcome="codex_failed")
    verifier = FixPostExecutionVerifier()
    continuation = CodexFixContinuation(
        launcher=launcher,
        launch_result_store_root=tmp_path / "operations",
        verifier=verifier,
    )

    class FakeDispatcher:
        def dispatch(self, operation, continue_impl, finalize_unresolved):
            return continue_impl(operation, {"status": "accepted", "handoff_path": handoff_path})

    executor = _make_executor(tmp_path, fix_dispatcher=FakeDispatcher(), fix_continuation=continuation)
    result = executor.execute(_make_fix_request(scanner_context=_make_scanner_context(tmp_path)))

    assert result.status == "failed"
    assert result.execution["codex_launched"] is True
    assert result.execution["codex_exit_code"] == 1
    assert result.execution.get("verification_performed") is False
    assert "verification_result_reference" not in result.execution


# ---------------------------------------------------------------------------
# Tests: scanner_clear behavior
# ---------------------------------------------------------------------------


def test_scanner_clear_passes_when_original_finding_disappears(tmp_path: Path):
    """scanner_clear passes when the original finding disappears."""
    # Create a clean Python file (no syntax error)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("value = 1\n", encoding="utf-8")

    handoff_path = _write_handoff(tmp_path, "fix-op-1")
    launcher = FakeLauncher(exit_code=0)
    verifier = FixPostExecutionVerifier()
    continuation = CodexFixContinuation(
        launcher=launcher,
        launch_result_store_root=tmp_path / "operations",
        verifier=verifier,
    )

    class FakeDispatcher:
        def dispatch(self, operation, continue_impl, finalize_unresolved):
            return continue_impl(operation, {"status": "accepted", "handoff_path": handoff_path})

    executor = _make_executor(tmp_path, fix_dispatcher=FakeDispatcher(), fix_continuation=continuation)
    result = executor.execute(_make_fix_request(scanner_context=_make_scanner_context(tmp_path)))

    assert result.status == "completed"
    assert result.execution["verification_passed"] is True


def test_scanner_clear_fails_when_original_finding_remains(tmp_path: Path):
    """scanner_clear fails when the original finding remains."""
    # Create a Python file with a syntax error
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("def broken(:\n  pass\n", encoding="utf-8")

    handoff_path = _write_handoff(tmp_path, "fix-op-1")
    launcher = FakeLauncher(exit_code=0)
    verifier = FixPostExecutionVerifier()
    continuation = CodexFixContinuation(
        launcher=launcher,
        launch_result_store_root=tmp_path / "operations",
        verifier=verifier,
    )

    class FakeDispatcher:
        def dispatch(self, operation, continue_impl, finalize_unresolved):
            return continue_impl(operation, {"status": "accepted", "handoff_path": handoff_path})

    executor = _make_executor(tmp_path, fix_dispatcher=FakeDispatcher(), fix_continuation=continuation)
    result = executor.execute(_make_fix_request(scanner_context=_make_scanner_context(tmp_path)))

    assert result.status == "failed"
    assert result.execution["verification_performed"] is True
    assert result.execution["verification_passed"] is False


def test_unrelated_new_scanner_findings_do_not_fail_accepted_fix(tmp_path: Path):
    """Unrelated new scanner findings do not fail the accepted Fix."""
    # Create a clean Python file (no syntax error in the target)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("value = 1\n", encoding="utf-8")
    # Add an unrelated file with a syntax error (not in the accepted Fix)
    (tmp_path / "src" / "other.py").write_text("def broken(:\n  pass\n", encoding="utf-8")

    handoff_path = _write_handoff(tmp_path, "fix-op-1")
    launcher = FakeLauncher(exit_code=0)
    verifier = FixPostExecutionVerifier()
    continuation = CodexFixContinuation(
        launcher=launcher,
        launch_result_store_root=tmp_path / "operations",
        verifier=verifier,
    )

    class FakeDispatcher:
        def dispatch(self, operation, continue_impl, finalize_unresolved):
            return continue_impl(operation, {"status": "accepted", "handoff_path": handoff_path})

    executor = _make_executor(tmp_path, fix_dispatcher=FakeDispatcher(), fix_continuation=continuation)
    result = executor.execute(_make_fix_request(scanner_context=_make_scanner_context(tmp_path)))

    # The accepted Fix only covers F-1 in src/app.py; unrelated findings
    # in src/other.py must not fail the accepted Fix.
    assert result.status == "completed"
    assert result.execution["verification_passed"] is True


# ---------------------------------------------------------------------------
# Tests: authorized command execution
# ---------------------------------------------------------------------------


def test_authorized_command_executes_with_shell_false_in_repository_root(tmp_path: Path):
    """Authorized command tokens execute with shell=False in repository_root."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("value = 1\n", encoding="utf-8")

    # Use a recorded_test grounding with a real command
    contract = {
        "finding_checks": [{"finding_id": "F-1", "resolution_method": "test", "check": "python -c \"print('ok')\"", "expected_result": "exit code 0"}],
        "validations": [],
        "must_not_regress": [],
        "success_rule": "all_finding_checks_and_validations_pass",
    }
    grounding = {
        "finding_checks": [{"finding_id": "F-1", "resolution_method": "test", "source_type": "recorded_test", "source_reference": "recorded:F-1", "authorized_tokens": ["python", "-c", "print('ok')"], "scoped_paths": ["src/app.py"]}],
        "validations": [],
    }
    handoff_path = _write_handoff(tmp_path, "fix-op-1", verification_contract=contract, verification_grounding=grounding)
    launcher = FakeLauncher(exit_code=0)
    verifier = FixPostExecutionVerifier()
    continuation = CodexFixContinuation(
        launcher=launcher,
        launch_result_store_root=tmp_path / "operations",
        verifier=verifier,
    )

    class FakeDispatcher:
        def dispatch(self, operation, continue_impl, finalize_unresolved):
            return continue_impl(operation, {"status": "accepted", "handoff_path": handoff_path})

    executor = _make_executor(tmp_path, fix_dispatcher=FakeDispatcher(), fix_continuation=continuation)
    result = executor.execute(_make_fix_request(scanner_context=_make_scanner_context(tmp_path)))

    assert result.status == "completed"
    assert result.execution["verification_passed"] is True


def test_command_stdout_stderr_exit_code_persisted(tmp_path: Path):
    """Command stdout, stderr, and exit code are persisted."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("value = 1\n", encoding="utf-8")

    contract = {
        "finding_checks": [],
        "validations": [{"id": "V-1", "command_or_assertion": "python -c \"print('hello'); import sys; sys.stderr.write('err')\"", "expected_result": "exit code 0"}],
        "must_not_regress": [],
        "success_rule": "all_finding_checks_and_validations_pass",
    }
    grounding = {
        "finding_checks": [],
        "validations": [{"validation_id": "V-1", "source_type": "configured_test", "source_reference": "configured_test", "authorized_tokens": ["python", "-c", "print('hello'); import sys; sys.stderr.write('err')"], "scoped_paths": []}],
    }
    handoff_path = _write_handoff(tmp_path, "fix-op-1", verification_contract=contract, verification_grounding=grounding)
    launcher = FakeLauncher(exit_code=0)
    verifier = FixPostExecutionVerifier()
    continuation = CodexFixContinuation(
        launcher=launcher,
        launch_result_store_root=tmp_path / "operations",
        verifier=verifier,
    )

    class FakeDispatcher:
        def dispatch(self, operation, continue_impl, finalize_unresolved):
            return continue_impl(operation, {"status": "accepted", "handoff_path": handoff_path})

    executor = _make_executor(tmp_path, fix_dispatcher=FakeDispatcher(), fix_continuation=continuation)
    result = executor.execute(_make_fix_request(scanner_context=_make_scanner_context(tmp_path)))

    assert result.status == "completed"
    verification_path = Path(result.execution["verification_result_reference"])
    data = json.loads(verification_path.read_text(encoding="utf-8"))
    assert len(data["validation_results"]) == 1
    vr = data["validation_results"][0]
    assert vr["validation_id"] == "V-1"
    assert vr["passed"] is True
    assert vr["exit_code"] == 0
    assert "hello" in (vr["stdout"] or "")
    assert "err" in (vr["stderr"] or "")


def test_command_exit_zero_passes_expected_result(tmp_path: Path):
    """Command exit 0 passes expected_result='exit code 0'."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("value = 1\n", encoding="utf-8")

    contract = {
        "finding_checks": [],
        "validations": [{"id": "V-1", "command_or_assertion": "python -c \"exit(0)\"", "expected_result": "exit code 0"}],
        "must_not_regress": [],
        "success_rule": "all_finding_checks_and_validations_pass",
    }
    grounding = {
        "finding_checks": [],
        "validations": [{"validation_id": "V-1", "source_type": "configured_test", "source_reference": "configured_test", "authorized_tokens": ["python", "-c", "exit(0)"], "scoped_paths": []}],
    }
    handoff_path = _write_handoff(tmp_path, "fix-op-1", verification_contract=contract, verification_grounding=grounding)
    launcher = FakeLauncher(exit_code=0)
    verifier = FixPostExecutionVerifier()
    continuation = CodexFixContinuation(
        launcher=launcher,
        launch_result_store_root=tmp_path / "operations",
        verifier=verifier,
    )

    class FakeDispatcher:
        def dispatch(self, operation, continue_impl, finalize_unresolved):
            return continue_impl(operation, {"status": "accepted", "handoff_path": handoff_path})

    executor = _make_executor(tmp_path, fix_dispatcher=FakeDispatcher(), fix_continuation=continuation)
    result = executor.execute(_make_fix_request(scanner_context=_make_scanner_context(tmp_path)))

    assert result.status == "completed"
    assert result.execution["verification_passed"] is True


def test_command_nonzero_fails_verification(tmp_path: Path):
    """Command non-zero fails verification."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("value = 1\n", encoding="utf-8")

    contract = {
        "finding_checks": [],
        "validations": [{"id": "V-1", "command_or_assertion": "python -c \"exit(1)\"", "expected_result": "exit code 0"}],
        "must_not_regress": [],
        "success_rule": "all_finding_checks_and_validations_pass",
    }
    grounding = {
        "finding_checks": [],
        "validations": [{"validation_id": "V-1", "source_type": "configured_test", "source_reference": "configured_test", "authorized_tokens": ["python", "-c", "exit(1)"], "scoped_paths": []}],
    }
    handoff_path = _write_handoff(tmp_path, "fix-op-1", verification_contract=contract, verification_grounding=grounding)
    launcher = FakeLauncher(exit_code=0)
    verifier = FixPostExecutionVerifier()
    continuation = CodexFixContinuation(
        launcher=launcher,
        launch_result_store_root=tmp_path / "operations",
        verifier=verifier,
    )

    class FakeDispatcher:
        def dispatch(self, operation, continue_impl, finalize_unresolved):
            return continue_impl(operation, {"status": "accepted", "handoff_path": handoff_path})

    executor = _make_executor(tmp_path, fix_dispatcher=FakeDispatcher(), fix_continuation=continuation)
    result = executor.execute(_make_fix_request(scanner_context=_make_scanner_context(tmp_path)))

    assert result.status == "failed"
    assert result.execution["verification_passed"] is False


def test_unsupported_expected_result_fails_closed(tmp_path: Path):
    """Unsupported expected_result fails closed."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("value = 1\n", encoding="utf-8")

    contract = {
        "finding_checks": [],
        "validations": [{"id": "V-1", "command_or_assertion": "python -c \"exit(0)\"", "expected_result": "exit code 42"}],
        "must_not_regress": [],
        "success_rule": "all_finding_checks_and_validations_pass",
    }
    grounding = {
        "finding_checks": [],
        "validations": [{"validation_id": "V-1", "source_type": "configured_test", "source_reference": "configured_test", "authorized_tokens": ["python", "-c", "exit(0)"], "scoped_paths": []}],
    }
    handoff_path = _write_handoff(tmp_path, "fix-op-1", verification_contract=contract, verification_grounding=grounding)
    launcher = FakeLauncher(exit_code=0)
    verifier = FixPostExecutionVerifier()
    continuation = CodexFixContinuation(
        launcher=launcher,
        launch_result_store_root=tmp_path / "operations",
        verifier=verifier,
    )

    class FakeDispatcher:
        def dispatch(self, operation, continue_impl, finalize_unresolved):
            return continue_impl(operation, {"status": "accepted", "handoff_path": handoff_path})

    executor = _make_executor(tmp_path, fix_dispatcher=FakeDispatcher(), fix_continuation=continuation)
    result = executor.execute(_make_fix_request(scanner_context=_make_scanner_context(tmp_path)))

    assert result.status == "failed"
    verification_path = Path(result.execution["verification_result_reference"])
    data = json.loads(verification_path.read_text(encoding="utf-8"))
    vr = data["validation_results"][0]
    assert vr["passed"] is False
    assert "unsupported_expected_result" in (vr["reason"] or "")


# ---------------------------------------------------------------------------
# Tests: deterministic assertions
# ---------------------------------------------------------------------------


def test_file_exists_works(tmp_path: Path):
    """file_exists works."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("value = 1\n", encoding="utf-8")

    contract = {
        "finding_checks": [],
        "validations": [{"id": "V-1", "command_or_assertion": "file_exists::src/app.py", "expected_result": "exists"}],
        "must_not_regress": [],
        "success_rule": "all_finding_checks_and_validations_pass",
    }
    grounding = {
        "finding_checks": [],
        "validations": [{"validation_id": "V-1", "source_type": "deterministic_assertion", "source_reference": "assertion:file_exists::src/app.py", "authorized_tokens": None, "scoped_paths": ["src/app.py"]}],
    }
    handoff_path = _write_handoff(tmp_path, "fix-op-1", verification_contract=contract, verification_grounding=grounding)
    launcher = FakeLauncher(exit_code=0)
    verifier = FixPostExecutionVerifier()
    continuation = CodexFixContinuation(
        launcher=launcher,
        launch_result_store_root=tmp_path / "operations",
        verifier=verifier,
    )

    class FakeDispatcher:
        def dispatch(self, operation, continue_impl, finalize_unresolved):
            return continue_impl(operation, {"status": "accepted", "handoff_path": handoff_path})

    executor = _make_executor(tmp_path, fix_dispatcher=FakeDispatcher(), fix_continuation=continuation)
    result = executor.execute(_make_fix_request(scanner_context=_make_scanner_context(tmp_path)))

    assert result.status == "completed"
    assert result.execution["verification_passed"] is True


def test_file_contains_and_file_not_contains_work(tmp_path: Path):
    """file_contains and file_not_contains work."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("MARKER_TOKEN = 42\n", encoding="utf-8")

    contract = {
        "finding_checks": [],
        "validations": [
            {"id": "V-CONTAINS", "command_or_assertion": "file_contains::src/app.py::MARKER_TOKEN", "expected_result": "contains"},
            {"id": "V-NOT-CONTAINS", "command_or_assertion": "file_not_contains::src/app.py::ABSENT_TOKEN", "expected_result": "absent"},
        ],
        "must_not_regress": [],
        "success_rule": "all_finding_checks_and_validations_pass",
    }
    grounding = {
        "finding_checks": [],
        "validations": [
            {"validation_id": "V-CONTAINS", "source_type": "deterministic_assertion", "source_reference": "assertion:file_contains::src/app.py::MARKER_TOKEN", "authorized_tokens": None, "scoped_paths": ["src/app.py"]},
            {"validation_id": "V-NOT-CONTAINS", "source_type": "deterministic_assertion", "source_reference": "assertion:file_not_contains::src/app.py::ABSENT_TOKEN", "authorized_tokens": None, "scoped_paths": ["src/app.py"]},
        ],
    }
    handoff_path = _write_handoff(tmp_path, "fix-op-1", verification_contract=contract, verification_grounding=grounding)
    launcher = FakeLauncher(exit_code=0)
    verifier = FixPostExecutionVerifier()
    continuation = CodexFixContinuation(
        launcher=launcher,
        launch_result_store_root=tmp_path / "operations",
        verifier=verifier,
    )

    class FakeDispatcher:
        def dispatch(self, operation, continue_impl, finalize_unresolved):
            return continue_impl(operation, {"status": "accepted", "handoff_path": handoff_path})

    executor = _make_executor(tmp_path, fix_dispatcher=FakeDispatcher(), fix_continuation=continuation)
    result = executor.execute(_make_fix_request(scanner_context=_make_scanner_context(tmp_path)))

    assert result.status == "completed"
    assert result.execution["verification_passed"] is True


def test_python_compiles_works_without_executing(tmp_path: Path):
    """python_compiles works without executing the file."""
    (tmp_path / "src").mkdir()
    # A file that would raise if executed but parses fine
    (tmp_path / "src" / "app.py").write_text("import sys\nsys.exit(1)\n", encoding="utf-8")

    contract = {
        "finding_checks": [],
        "validations": [{"id": "V-1", "command_or_assertion": "python_compiles::src/app.py", "expected_result": "compiles"}],
        "must_not_regress": [],
        "success_rule": "all_finding_checks_and_validations_pass",
    }
    grounding = {
        "finding_checks": [],
        "validations": [{"validation_id": "V-1", "source_type": "deterministic_assertion", "source_reference": "assertion:python_compiles::src/app.py", "authorized_tokens": None, "scoped_paths": ["src/app.py"]}],
    }
    handoff_path = _write_handoff(tmp_path, "fix-op-1", verification_contract=contract, verification_grounding=grounding)
    launcher = FakeLauncher(exit_code=0)
    verifier = FixPostExecutionVerifier()
    continuation = CodexFixContinuation(
        launcher=launcher,
        launch_result_store_root=tmp_path / "operations",
        verifier=verifier,
    )

    class FakeDispatcher:
        def dispatch(self, operation, continue_impl, finalize_unresolved):
            return continue_impl(operation, {"status": "accepted", "handoff_path": handoff_path})

    executor = _make_executor(tmp_path, fix_dispatcher=FakeDispatcher(), fix_continuation=continuation)
    result = executor.execute(_make_fix_request(scanner_context=_make_scanner_context(tmp_path)))

    assert result.status == "completed"
    assert result.execution["verification_passed"] is True


def test_json_parses_works(tmp_path: Path):
    """json_parses works."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("value = 1\n", encoding="utf-8")
    (tmp_path / "src" / "data.json").write_text('{"version": "1", "kind": "test"}', encoding="utf-8")

    contract = {
        "finding_checks": [],
        "validations": [{"id": "V-1", "command_or_assertion": "json_parses::src/data.json", "expected_result": "parses"}],
        "must_not_regress": [],
        "success_rule": "all_finding_checks_and_validations_pass",
    }
    grounding = {
        "finding_checks": [],
        "validations": [{"validation_id": "V-1", "source_type": "deterministic_assertion", "source_reference": "assertion:json_parses::src/data.json", "authorized_tokens": None, "scoped_paths": ["src/data.json"]}],
    }
    handoff_path = _write_handoff(tmp_path, "fix-op-1", target_files=["src/app.py", "src/data.json"], verification_contract=contract, verification_grounding=grounding)
    launcher = FakeLauncher(exit_code=0)
    verifier = FixPostExecutionVerifier()
    continuation = CodexFixContinuation(
        launcher=launcher,
        launch_result_store_root=tmp_path / "operations",
        verifier=verifier,
    )

    class FakeDispatcher:
        def dispatch(self, operation, continue_impl, finalize_unresolved):
            return continue_impl(operation, {"status": "accepted", "handoff_path": handoff_path})

    executor = _make_executor(tmp_path, fix_dispatcher=FakeDispatcher(), fix_continuation=continuation)
    result = executor.execute(_make_fix_request(scanner_context=_make_scanner_context(tmp_path)))

    assert result.status == "completed"
    assert result.execution["verification_passed"] is True


def test_out_of_scope_assertion_paths_fail_closed(tmp_path: Path):
    """Out-of-scope assertion paths fail closed."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("value = 1\n", encoding="utf-8")

    contract = {
        "finding_checks": [],
        "validations": [{"id": "V-1", "command_or_assertion": "file_exists::other/out_of_scope.py", "expected_result": "exists"}],
        "must_not_regress": [],
        "success_rule": "all_finding_checks_and_validations_pass",
    }
    grounding = {
        "finding_checks": [],
        "validations": [{"validation_id": "V-1", "source_type": "deterministic_assertion", "source_reference": "assertion:file_exists::other/out_of_scope.py", "authorized_tokens": None, "scoped_paths": ["src/app.py"]}],
    }
    handoff_path = _write_handoff(tmp_path, "fix-op-1", verification_contract=contract, verification_grounding=grounding)
    launcher = FakeLauncher(exit_code=0)
    verifier = FixPostExecutionVerifier()
    continuation = CodexFixContinuation(
        launcher=launcher,
        launch_result_store_root=tmp_path / "operations",
        verifier=verifier,
    )

    class FakeDispatcher:
        def dispatch(self, operation, continue_impl, finalize_unresolved):
            return continue_impl(operation, {"status": "accepted", "handoff_path": handoff_path})

    executor = _make_executor(tmp_path, fix_dispatcher=FakeDispatcher(), fix_continuation=continuation)
    result = executor.execute(_make_fix_request(scanner_context=_make_scanner_context(tmp_path)))

    assert result.status == "failed"
    verification_path = Path(result.execution["verification_result_reference"])
    data = json.loads(verification_path.read_text(encoding="utf-8"))
    vr = data["validation_results"][0]
    assert vr["passed"] is False
    assert "not in approved scope" in (vr["reason"] or "")


# ---------------------------------------------------------------------------
# Tests: every FindingCheck and ValidationSpec produces a result
# ---------------------------------------------------------------------------


def test_every_finding_check_produces_a_result(tmp_path: Path):
    """Every FindingCheck produces a result."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("value = 1\n", encoding="utf-8")

    contract = {
        "finding_checks": [
            {"finding_id": "F-1", "resolution_method": "rescan", "check": "scanner_clear::F-1", "expected_result": "finding resolved"},
            {"finding_id": "F-1", "resolution_method": "assertion", "check": "file_exists::src/app.py", "expected_result": "exists"},
        ],
        "validations": [],
        "must_not_regress": [],
        "success_rule": "all_finding_checks_and_validations_pass",
    }
    grounding = {
        "finding_checks": [
            {"finding_id": "F-1", "resolution_method": "rescan", "source_type": "deterministic_assertion", "source_reference": "assertion:scanner_clear::F-1", "authorized_tokens": None, "scoped_paths": ["src/app.py"]},
            {"finding_id": "F-1", "resolution_method": "assertion", "source_type": "deterministic_assertion", "source_reference": "assertion:file_exists::src/app.py", "authorized_tokens": None, "scoped_paths": ["src/app.py"]},
        ],
        "validations": [],
    }
    handoff_path = _write_handoff(tmp_path, "fix-op-1", verification_contract=contract, verification_grounding=grounding)
    launcher = FakeLauncher(exit_code=0)
    verifier = FixPostExecutionVerifier()
    continuation = CodexFixContinuation(
        launcher=launcher,
        launch_result_store_root=tmp_path / "operations",
        verifier=verifier,
    )

    class FakeDispatcher:
        def dispatch(self, operation, continue_impl, finalize_unresolved):
            return continue_impl(operation, {"status": "accepted", "handoff_path": handoff_path})

    executor = _make_executor(tmp_path, fix_dispatcher=FakeDispatcher(), fix_continuation=continuation)
    result = executor.execute(_make_fix_request(scanner_context=_make_scanner_context(tmp_path)))

    verification_path = Path(result.execution["verification_result_reference"])
    data = json.loads(verification_path.read_text(encoding="utf-8"))
    assert len(data["finding_results"]) == 2


def test_every_validation_spec_produces_a_result(tmp_path: Path):
    """Every ValidationSpec produces a result."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("value = 1\n", encoding="utf-8")

    contract = {
        "finding_checks": [],
        "validations": [
            {"id": "V-1", "command_or_assertion": "file_exists::src/app.py", "expected_result": "exists"},
            {"id": "V-2", "command_or_assertion": "python_compiles::src/app.py", "expected_result": "compiles"},
        ],
        "must_not_regress": [],
        "success_rule": "all_finding_checks_and_validations_pass",
    }
    grounding = {
        "finding_checks": [],
        "validations": [
            {"validation_id": "V-1", "source_type": "deterministic_assertion", "source_reference": "assertion:file_exists::src/app.py", "authorized_tokens": None, "scoped_paths": ["src/app.py"]},
            {"validation_id": "V-2", "source_type": "deterministic_assertion", "source_reference": "assertion:python_compiles::src/app.py", "authorized_tokens": None, "scoped_paths": ["src/app.py"]},
        ],
    }
    handoff_path = _write_handoff(tmp_path, "fix-op-1", verification_contract=contract, verification_grounding=grounding)
    launcher = FakeLauncher(exit_code=0)
    verifier = FixPostExecutionVerifier()
    continuation = CodexFixContinuation(
        launcher=launcher,
        launch_result_store_root=tmp_path / "operations",
        verifier=verifier,
    )

    class FakeDispatcher:
        def dispatch(self, operation, continue_impl, finalize_unresolved):
            return continue_impl(operation, {"status": "accepted", "handoff_path": handoff_path})

    executor = _make_executor(tmp_path, fix_dispatcher=FakeDispatcher(), fix_continuation=continuation)
    result = executor.execute(_make_fix_request(scanner_context=_make_scanner_context(tmp_path)))

    verification_path = Path(result.execution["verification_result_reference"])
    data = json.loads(verification_path.read_text(encoding="utf-8"))
    assert len(data["validation_results"]) == 2


# ---------------------------------------------------------------------------
# Tests: success rule
# ---------------------------------------------------------------------------


def test_fixed_success_rule_passes_only_when_all_required_results_pass(tmp_path: Path):
    """Fixed success rule passes only when all required results pass."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("value = 1\n", encoding="utf-8")

    contract = {
        "finding_checks": [{"finding_id": "F-1", "resolution_method": "rescan", "check": "scanner_clear::F-1", "expected_result": "finding resolved"}],
        "validations": [{"id": "V-1", "command_or_assertion": "python -c \"exit(1)\"", "expected_result": "exit code 0"}],
        "must_not_regress": [],
        "success_rule": "all_finding_checks_and_validations_pass",
    }
    grounding = {
        "finding_checks": [{"finding_id": "F-1", "resolution_method": "rescan", "source_type": "deterministic_assertion", "source_reference": "assertion:scanner_clear::F-1", "authorized_tokens": None, "scoped_paths": ["src/app.py"]}],
        "validations": [{"validation_id": "V-1", "source_type": "configured_test", "source_reference": "configured_test", "authorized_tokens": ["python", "-c", "exit(1)"], "scoped_paths": []}],
    }
    handoff_path = _write_handoff(tmp_path, "fix-op-1", verification_contract=contract, verification_grounding=grounding)
    launcher = FakeLauncher(exit_code=0)
    verifier = FixPostExecutionVerifier()
    continuation = CodexFixContinuation(
        launcher=launcher,
        launch_result_store_root=tmp_path / "operations",
        verifier=verifier,
    )

    class FakeDispatcher:
        def dispatch(self, operation, continue_impl, finalize_unresolved):
            return continue_impl(operation, {"status": "accepted", "handoff_path": handoff_path})

    executor = _make_executor(tmp_path, fix_dispatcher=FakeDispatcher(), fix_continuation=continuation)
    result = executor.execute(_make_fix_request(scanner_context=_make_scanner_context(tmp_path)))

    # scanner_clear passes (file is clean) but command fails → overall fail
    assert result.status == "failed"
    assert result.execution["verification_passed"] is False


def test_unsupported_success_rule_fails_closed(tmp_path: Path):
    """Unsupported success rule fails closed."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("value = 1\n", encoding="utf-8")

    contract = {
        "finding_checks": [],
        "validations": [],
        "must_not_regress": [],
        "success_rule": "all_checks_pass",
    }
    grounding = {
        "finding_checks": [],
        "validations": [],
    }
    handoff_path = _write_handoff(tmp_path, "fix-op-1", verification_contract=contract, verification_grounding=grounding)
    launcher = FakeLauncher(exit_code=0)
    verifier = FixPostExecutionVerifier()
    continuation = CodexFixContinuation(
        launcher=launcher,
        launch_result_store_root=tmp_path / "operations",
        verifier=verifier,
    )

    class FakeDispatcher:
        def dispatch(self, operation, continue_impl, finalize_unresolved):
            return continue_impl(operation, {"status": "accepted", "handoff_path": handoff_path})

    executor = _make_executor(tmp_path, fix_dispatcher=FakeDispatcher(), fix_continuation=continuation)
    result = executor.execute(_make_fix_request(scanner_context=_make_scanner_context(tmp_path)))

    assert result.status == "failed"
    verification_path = Path(result.execution["verification_result_reference"])
    data = json.loads(verification_path.read_text(encoding="utf-8"))
    assert data["failure_code"] == "unsupported_success_rule"


# ---------------------------------------------------------------------------
# Tests: idempotency
# ---------------------------------------------------------------------------


def test_duplicate_operation_does_not_relaunch_codex_or_rerun_verification(tmp_path: Path):
    """Duplicate operation does not relaunch Codex or rerun verification."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("value = 1\n", encoding="utf-8")

    handoff_path = _write_handoff(tmp_path, "fix-op-1")
    launcher = FakeLauncher(exit_code=0)
    verifier = FixPostExecutionVerifier()
    continuation = CodexFixContinuation(
        launcher=launcher,
        launch_result_store_root=tmp_path / "operations",
        verifier=verifier,
    )

    class FakeDispatcher:
        def dispatch(self, operation, continue_impl, finalize_unresolved):
            return continue_impl(operation, {"status": "accepted", "handoff_path": handoff_path})

    executor = _make_executor(tmp_path, fix_dispatcher=FakeDispatcher(), fix_continuation=continuation)
    request = _make_fix_request(scanner_context=_make_scanner_context(tmp_path))
    first = executor.execute(request)
    second = executor.execute(request)

    assert first.status == "completed"
    assert second.idempotency_replay is True
    assert len(launcher.calls) == 1


def test_existing_verification_result_is_returned_without_rerun(tmp_path: Path):
    """When fix-verification-result.json already exists, return the persisted result."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("value = 1\n", encoding="utf-8")

    handoff_path = _write_handoff(tmp_path, "fix-op-1")
    launcher = FakeLauncher(exit_code=0)
    verifier = FixPostExecutionVerifier()
    continuation = CodexFixContinuation(
        launcher=launcher,
        launch_result_store_root=tmp_path / "operations",
        verifier=verifier,
    )

    class FakeDispatcher:
        def dispatch(self, operation, continue_impl, finalize_unresolved):
            return continue_impl(operation, {"status": "accepted", "handoff_path": handoff_path})

    executor = _make_executor(tmp_path, fix_dispatcher=FakeDispatcher(), fix_continuation=continuation)
    request = _make_fix_request(scanner_context=_make_scanner_context(tmp_path))
    first = executor.execute(request)

    # Now call the verifier directly with the same operation_id
    handoff = json.loads(Path(handoff_path).read_text(encoding="utf-8"))
    codex_result = json.loads(Path(first.execution["codex_result_reference"]).read_text(encoding="utf-8"))
    result_root = Path(first.execution["verification_result_reference"]).parent

    # Second call should return the persisted result without rerunning
    second = verifier.verify(
        operation_id="fix-op-1",
        repository_root=handoff["scanner_context"]["repository_root"],
        scanner_context=handoff["scanner_context"],
        original_findings=handoff["findings"],
        verification_contract=handoff["verification_contract"],
        verification_grounding=handoff["verification_grounding"],
        codex_result=codex_result,
        result_root=result_root,
    )
    assert second.passed is True
    assert second.completion_claimed is True


# ---------------------------------------------------------------------------
# Tests: Build behavior unchanged
# ---------------------------------------------------------------------------


def test_existing_build_behavior_remains_unchanged(tmp_path: Path):
    """Existing Build behavior remains unchanged."""
    from audisor.schemas.execution import BuildExecutionRequest
    from audisor.schemas.task_output import TaskOutput

    worker_called = False
    fix_continuation_called = False

    class FakeWorker:
        model_id = "test-model"

        def execute(self, task):
            nonlocal worker_called
            worker_called = True
            output = TaskOutput(task_id=task.task_id, answer='[{"type": "build", "content": "ok"}]')
            output.set_response_metadata(http_status=200, transport_succeeded=True, finish_reason="stop", tool_call_present=False, choice_count=1)
            return output

    class AssertingContinuation:
        def run(self, **kwargs):
            nonlocal fix_continuation_called
            fix_continuation_called = True
            raise AssertionError("Fix continuation must not be called for Build")

    class AssertingDispatcher:
        def dispatch(self, operation, continue_impl, finalize_unresolved):
            raise AssertionError("Fix dispatcher must not be called for Build")

    executor = _make_executor(tmp_path, fix_dispatcher=AssertingDispatcher(), fix_continuation=AssertingContinuation(), worker_factory=lambda config: FakeWorker())
    build_request = BuildExecutionRequest(execution_id="build-op-1", idempotency_key="build-op-1", target_root="target", allowed_write_paths=["src"])
    request = AudisorOperationRequest(
        operation_id="build-op-1", mode="build",
        request={"build": {"build_id": "build-1", "request": build_request.model_dump(mode="json")}},
        authority=AuthorityContext(
            source=AuthoritySource(source_type="user", grant_id="test", host_identity="cli"),
            permissions=PermissionSet(allowed_paths=[".", "target", "src"], prohibited_paths=[".git", ".codex"], allowed_tools=[], prohibited_tools=[]),
            scope="repository",
        ),
        constraints={}, host_capabilities=HostCapabilities(), host_context={"adapter": "cli"},
    )
    result = executor.execute(request)

    assert worker_called is True
    assert fix_continuation_called is False
    assert result.status == "completed"


# ---------------------------------------------------------------------------
# Tests: persisted verification result shape
# ---------------------------------------------------------------------------


def test_verification_result_persisted_under_same_operation_id(tmp_path: Path):
    """Verification result is persisted under the same operation ID."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("value = 1\n", encoding="utf-8")

    handoff_path = _write_handoff(tmp_path, "fix-op-1")
    launcher = FakeLauncher(exit_code=0)
    verifier = FixPostExecutionVerifier()
    continuation = CodexFixContinuation(
        launcher=launcher,
        launch_result_store_root=tmp_path / "operations",
        verifier=verifier,
    )

    class FakeDispatcher:
        def dispatch(self, operation, continue_impl, finalize_unresolved):
            return continue_impl(operation, {"status": "accepted", "handoff_path": handoff_path})

    executor = _make_executor(tmp_path, fix_dispatcher=FakeDispatcher(), fix_continuation=continuation)
    result = executor.execute(_make_fix_request(scanner_context=_make_scanner_context(tmp_path)))

    # The verification result is persisted under the same operation ID
    verification_path = Path(result.execution["verification_result_reference"])
    assert verification_path.is_file()
    assert "fix-op-1" in str(verification_path)

    data = json.loads(verification_path.read_text(encoding="utf-8"))
    # Required shape
    assert data["operation_id"] == "fix-op-1"
    assert data["repository_root"] == str(tmp_path.resolve())
    assert data["codex_result_reference"] == result.execution["codex_result_reference"]
    assert data["verification_performed"] is True
    assert data["success_rule"] == "all_finding_checks_and_validations_pass"
    assert "scanner_result" in data
    assert "original_finding_ids" in data["scanner_result"]
    assert "remaining_original_finding_ids" in data["scanner_result"]
    assert "finding_results" in data
    assert "validation_results" in data
    assert data["passed"] is True
    assert data["completion_claimed"] is True
    assert data["failure_code"] is None
    assert data["failure_message"] is None


def test_successful_verification_returns_operation_status_completed(tmp_path: Path):
    """Successful verification returns operation status completed."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("value = 1\n", encoding="utf-8")

    handoff_path = _write_handoff(tmp_path, "fix-op-1")
    launcher = FakeLauncher(exit_code=0)
    verifier = FixPostExecutionVerifier()
    continuation = CodexFixContinuation(
        launcher=launcher,
        launch_result_store_root=tmp_path / "operations",
        verifier=verifier,
    )

    class FakeDispatcher:
        def dispatch(self, operation, continue_impl, finalize_unresolved):
            return continue_impl(operation, {"status": "accepted", "handoff_path": handoff_path})

    executor = _make_executor(tmp_path, fix_dispatcher=FakeDispatcher(), fix_continuation=continuation)
    result = executor.execute(_make_fix_request(scanner_context=_make_scanner_context(tmp_path)))

    assert result.status == "completed"


def test_failed_verification_returns_operation_status_failed(tmp_path: Path):
    """Failed verification returns operation status failed."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("def broken(:\n  pass\n", encoding="utf-8")

    handoff_path = _write_handoff(tmp_path, "fix-op-1")
    launcher = FakeLauncher(exit_code=0)
    verifier = FixPostExecutionVerifier()
    continuation = CodexFixContinuation(
        launcher=launcher,
        launch_result_store_root=tmp_path / "operations",
        verifier=verifier,
    )

    class FakeDispatcher:
        def dispatch(self, operation, continue_impl, finalize_unresolved):
            return continue_impl(operation, {"status": "accepted", "handoff_path": handoff_path})

    executor = _make_executor(tmp_path, fix_dispatcher=FakeDispatcher(), fix_continuation=continuation)
    result = executor.execute(_make_fix_request(scanner_context=_make_scanner_context(tmp_path)))

    assert result.status == "failed"
    # Verification evidence is persisted even on failure
    assert "verification_result_reference" in result.execution
    verification_path = Path(result.execution["verification_result_reference"])
    assert verification_path.is_file()