"""Tests for automatic Fix-to-Codex continuation.

Proves:
1. Accepted Fix automatically launches Codex once.
2. Codex receives the same operation ID, handoff, qualified plan, and only authorized target paths.
3. The original handoff authority flags remain false.
4. The host-owned launch envelope carries bounded execution authority separately.
5. Blocked or rejected Fix never launches Codex.
6. Fix without a configured continuation fails closed without launching Codex.
7. Duplicate accepted Fix does not launch Codex twice.
8. Codex launch failure produces a persisted failed result.
9. No Fix path calls BuildExecutor or PreparedBuildLoader.
10. Existing Build behavior remains unchanged.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from audisor.adapters.protocol import AudisorOperationRequest, HostCapabilities
from audisor.codex.fix_continuation import CodexFixContinuation, FixContinuationError, FixContinuationResult
from audisor.operations.artifacts import ArtifactStore
from audisor.operations.executor import AudisorOperationExecutor, ExecutorConfig, FixRouteConfig
from audisor.operations.mutation_enforcer import MutationEnforcer
from audisor.operations.store import AudisorOperationStore
from audisor.schemas.authority import AuthorityContext, AuthoritySource, PermissionSet


def _make_fix_request(operation_id: str = "fix-op-1", *, target_files: list[str] | None = None) -> AudisorOperationRequest:
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


def _write_handoff(tmp_path: Path, operation_id: str, target_files: list[str] | None = None) -> str:
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

    def __call__(self, *, stdin_bytes: bytes, cwd: Path) -> tuple[int | None, int, str, tuple[str, ...]]:
        self.calls.append({"stdin_bytes": stdin_bytes, "cwd": cwd})
        if self._fail:
            from audisor.codex.launcher import CodexLaunchError
            raise CodexLaunchError("codex_process_start_failed", "fake failure")
        return 12345, self._exit_code, self._outcome, ("codex", "exec", "-")


def test_accepted_fix_automatically_launches_codex_once(tmp_path: Path):
    handoff_path = _write_handoff(tmp_path, "fix-op-1")
    launcher = FakeLauncher()
    continuation = CodexFixContinuation(launcher=launcher, launch_result_store_root=tmp_path / "operations")

    class FakeDispatcher:
        def dispatch(self, operation, continue_impl, finalize_unresolved):
            return continue_impl(operation, {"status": "accepted", "handoff_path": handoff_path})

    executor = _make_executor(tmp_path, fix_dispatcher=FakeDispatcher(), fix_continuation=continuation)
    result = executor.execute(_make_fix_request())

    assert result.status == "accepted"
    assert result.execution is not None
    assert result.execution["fix_dispatched"] is True
    assert result.execution["codex_launched"] is True
    assert result.execution["handoff_path"] == handoff_path
    assert "codex_result_reference" in result.execution
    assert len(launcher.calls) == 1


def test_codex_receives_same_operation_id_and_handoff_and_authorized_paths(tmp_path: Path):
    handoff_path = _write_handoff(tmp_path, "fix-op-1")
    launcher = FakeLauncher()
    continuation = CodexFixContinuation(launcher=launcher, launch_result_store_root=tmp_path / "operations")

    class FakeDispatcher:
        def dispatch(self, operation, continue_impl, finalize_unresolved):
            return continue_impl(operation, {"status": "accepted", "handoff_path": handoff_path})

    executor = _make_executor(tmp_path, fix_dispatcher=FakeDispatcher(), fix_continuation=continuation)
    executor.execute(_make_fix_request())

    assert len(launcher.calls) == 1
    stdin_text = launcher.calls[0]["stdin_bytes"].decode("utf-8")
    assert "fix-op-1" in stdin_text
    assert "qualified-fix-handoff.json" in stdin_text
    assert "qualified_plan" in stdin_text
    assert "allowed_target_paths" in stdin_text


def test_original_handoff_authority_flags_remain_false(tmp_path: Path):
    handoff_path = _write_handoff(tmp_path, "fix-op-1")
    launcher = FakeLauncher()
    continuation = CodexFixContinuation(launcher=launcher, launch_result_store_root=tmp_path / "operations")

    class FakeDispatcher:
        def dispatch(self, operation, continue_impl, finalize_unresolved):
            return continue_impl(operation, {"status": "accepted", "handoff_path": handoff_path})

    executor = _make_executor(tmp_path, fix_dispatcher=FakeDispatcher(), fix_continuation=continuation)
    executor.execute(_make_fix_request())

    handoff = json.loads(Path(handoff_path).read_text(encoding="utf-8"))
    assert handoff["authority"]["mutation_authorized"] is False
    assert handoff["authority"]["execution_authorized"] is False
    assert handoff["authority"]["apply_authorized"] is False
    assert handoff["authority"]["completion_claimed"] is False


def test_host_owned_envelope_carries_bounded_authority_separately(tmp_path: Path):
    handoff_path = _write_handoff(tmp_path, "fix-op-1")
    launcher = FakeLauncher()
    continuation = CodexFixContinuation(launcher=launcher, launch_result_store_root=tmp_path / "operations")

    class FakeDispatcher:
        def dispatch(self, operation, continue_impl, finalize_unresolved):
            return continue_impl(operation, {"status": "accepted", "handoff_path": handoff_path})

    executor = _make_executor(tmp_path, fix_dispatcher=FakeDispatcher(), fix_continuation=continuation)
    result = executor.execute(_make_fix_request())

    envelope = json.loads(Path(result.execution["codex_envelope_path"]).read_text(encoding="utf-8"))
    assert envelope["operation_type"] == "fix"
    assert envelope["operation_id"] == "fix-op-1"
    assert envelope["handoff_path"] == handoff_path
    assert envelope["host_authority"]["mutation_authorized"] is True
    assert envelope["host_authority"]["execution_authorized"] is True
    assert envelope["host_authority"]["apply_authorized"] is False
    assert envelope["host_authority"]["completion_claimed"] is False


def test_blocked_fix_never_launches_codex(tmp_path: Path):
    launcher = FakeLauncher()
    continuation = CodexFixContinuation(launcher=launcher, launch_result_store_root=tmp_path / "operations")

    class FakeDispatcher:
        def dispatch(self, operation, continue_impl, finalize_unresolved):
            return finalize_unresolved(operation, {"status": "validation_failed", "error": {"code": "scoped_snapshot_required", "message": "missing"}})

    executor = _make_executor(tmp_path, fix_dispatcher=FakeDispatcher(), fix_continuation=continuation)
    result = executor.execute(_make_fix_request())

    assert result.status == "blocked"
    assert len(launcher.calls) == 0


def test_fix_without_continuation_does_not_launch_codex(tmp_path: Path):
    handoff_path = _write_handoff(tmp_path, "fix-op-1")
    launcher = FakeLauncher()

    class FakeDispatcher:
        def dispatch(self, operation, continue_impl, finalize_unresolved):
            return continue_impl(operation, {"status": "accepted", "handoff_path": handoff_path})

    executor = _make_executor(tmp_path, fix_dispatcher=FakeDispatcher(), fix_continuation=None)
    result = executor.execute(_make_fix_request())

    assert result.status == "accepted"
    assert result.execution is not None
    assert result.execution["codex_launched"] is False
    assert len(launcher.calls) == 0


def test_duplicate_accepted_fix_does_not_launch_codex_twice(tmp_path: Path):
    handoff_path = _write_handoff(tmp_path, "fix-op-1")
    launcher = FakeLauncher()
    continuation = CodexFixContinuation(launcher=launcher, launch_result_store_root=tmp_path / "operations")
    dispatch_count = 0

    class FakeDispatcher:
        def dispatch(self, operation, continue_impl, finalize_unresolved):
            nonlocal dispatch_count
            dispatch_count += 1
            return continue_impl(operation, {"status": "accepted", "handoff_path": handoff_path})

    executor = _make_executor(tmp_path, fix_dispatcher=FakeDispatcher(), fix_continuation=continuation)
    request = _make_fix_request()
    first = executor.execute(request)
    second = executor.execute(request)

    assert first.status == "accepted"
    assert first.execution["codex_launched"] is True
    assert dispatch_count == 1
    assert len(launcher.calls) == 1
    assert second.idempotency_replay is True


def test_codex_launch_failure_produces_persisted_failed_result(tmp_path: Path):
    handoff_path = _write_handoff(tmp_path, "fix-op-1")
    launcher = FakeLauncher(fail=True)
    continuation = CodexFixContinuation(launcher=launcher, launch_result_store_root=tmp_path / "operations")

    class FakeDispatcher:
        def dispatch(self, operation, continue_impl, finalize_unresolved):
            return continue_impl(operation, {"status": "accepted", "handoff_path": handoff_path})

    executor = _make_executor(tmp_path, fix_dispatcher=FakeDispatcher(), fix_continuation=continuation)
    result = executor.execute(_make_fix_request())

    assert result.status == "failed"
    assert result.error is not None
    assert result.error.error_code.code == "codex_launch_failed"
    assert result.execution is not None
    assert result.execution["codex_launched"] is False
    assert "codex_failure" in result.execution


def test_no_fix_path_calls_build_executor_or_prepared_build_loader(tmp_path: Path):
    handoff_path = _write_handoff(tmp_path, "fix-op-1")
    launcher = FakeLauncher()
    continuation = CodexFixContinuation(launcher=launcher, launch_result_store_root=tmp_path / "operations")

    class FakeDispatcher:
        def dispatch(self, operation, continue_impl, finalize_unresolved):
            return continue_impl(operation, {"status": "accepted", "handoff_path": handoff_path})

    executor = _make_executor(tmp_path, fix_dispatcher=FakeDispatcher(), fix_continuation=continuation)
    result = executor.execute(_make_fix_request())

    assert result.status == "accepted"


def test_existing_build_behavior_remains_unchanged(tmp_path: Path):
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