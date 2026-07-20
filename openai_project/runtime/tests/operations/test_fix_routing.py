"""Tests for canonical Fix routing through the AudisorOperationExecutor.

Proves:
- A complete Fix package reaches AcceptedFixDispatcher
- Every reconstructed nested value is preserved
- The dispatcher is invoked exactly once
- The generic LocalWorker is not constructed for Fix
- An accepted Fix exposes its qualified handoff reference
- An unresolved Fix blocks continuation
- Build still uses its existing execution path
- Duplicate Fix operation behavior remains idempotent through the existing store
- Fix without a configured route blocks without constructing a worker
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from audisor.adapters.protocol import AudisorOperationRequest, HostCapabilities
from audisor.operations.artifacts import ArtifactStore
from audisor.operations.executor import AudisorOperationExecutor, ExecutorConfig, FixRouteConfig
from audisor.operations.mutation_enforcer import MutationEnforcer
from audisor.operations.result import AudisorOperationResult
from audisor.operations.store import AudisorOperationStore
from audisor.schemas.authority import AuthorityContext, AuthoritySource, PermissionSet
from audisor.schemas.execution import BuildExecutionRequest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_fix_request(
    operation_id: str = "fix-op-1",
    *,
    findings: list[dict[str, Any]] | None = None,
    manifest: dict[str, Any] | None = None,
    statements: list[dict[str, Any]] | None = None,
    plan: dict[str, Any] | None = None,
    workspace_identity: dict[str, Any] | None = None,
    authority_context: dict[str, Any] | None = None,
    aflow_analysis_request: dict[str, Any] | None = None,
) -> AudisorOperationRequest:
    """Build a canonical AudisorOperationRequest with a complete Fix payload."""
    if findings is None:
        findings = [
            {
                "id": "F-1",
                "type": "syntax",
                "file": "src/app.py",
                "severity": "high",
                "evidence": {"line": 42, "message": "undefined variable"},
            }
        ]
    if manifest is None:
        manifest = {
            "files": ["src/app.py"],
            "dependency_closure": ["src/app.py", "src/utils.py"],
            "input_hash": "abc123",
            "file_hashes": {"src/app.py": "a" * 64, "src/utils.py": "b" * 64},
        }
    if statements is None:
        statements = [
            {
                "type": "mutation_authority",
                "content": {"authorized": True, "scope": "repository"},
                "findings_ref_hash": "findings-hash",
                "manifest_ref_hash": "manifest-hash",
            },
            {
                "type": "plan_authority",
                "content": {"qualified": True},
                "findings_ref_hash": "findings-hash",
                "manifest_ref_hash": "manifest-hash",
            },
            {
                "type": "execution_authority",
                "content": {"scope": "repository"},
                "findings_ref_hash": "findings-hash",
                "manifest_ref_hash": "manifest-hash",
            },
        ]
    if plan is None:
        plan = {
            "steps": [
                {
                    "id": "S-1",
                    "action": "repair",
                    "target_file": "src/app.py",
                    "originating_finding_id": "F-1",
                    "acceptance_criterion": "test passes",
                }
            ],
            "target_files": ["src/app.py"],
            "is_qualified": True,
            "minor_issues": [],
        }
    if workspace_identity is None:
        workspace_identity = {"path": "sandbox/fix-op-1", "root": "/repo"}
    if authority_context is None:
        authority_context = {"allowed_paths": ["src/app.py"], "scope": "repository"}

    fix_payload = {
        "operation_id": operation_id,
        "findings": findings,
        "manifest": manifest,
        "statements": statements,
        "plan": plan,
        "workspace_identity": workspace_identity,
        "authority_context": authority_context,
        "aflow_analysis_request": aflow_analysis_request,
    }

    return AudisorOperationRequest(
        operation_id=operation_id,
        mode="fix",
        request={"fix": fix_payload},
        authority=AuthorityContext(
            source=AuthoritySource(source_type="user", grant_id="test", host_identity="cli"),
            permissions=PermissionSet(
                allowed_paths=["."],
                prohibited_paths=[".git", ".codex"],
                allowed_tools=[],
                prohibited_tools=[],
            ),
            scope="repository",
        ),
        constraints={},
        host_capabilities=HostCapabilities(),
        host_context={"adapter": "cli"},
    )


def _make_build_request(operation_id: str = "build-op-1") -> AudisorOperationRequest:
    """Build a canonical AudisorOperationRequest for Build mode.

    Uses target paths that are authorized by the test authority's
    allowed_paths so the MutationEnforcer does not reject the build.
    """
    build_request = BuildExecutionRequest(
        execution_id=operation_id,
        idempotency_key=operation_id,
        target_root="target",
        allowed_write_paths=["src"],
    )
    return AudisorOperationRequest(
        operation_id=operation_id,
        mode="build",
        request={
            "build": {
                "build_id": "build-1",
                "request": build_request.model_dump(mode="json"),
            }
        },
        authority=AuthorityContext(
            source=AuthoritySource(source_type="user", grant_id="test", host_identity="cli"),
            permissions=PermissionSet(
                allowed_paths=[".", "target", "src"],
                prohibited_paths=[".git", ".codex"],
                allowed_tools=[],
                prohibited_tools=[],
            ),
            scope="repository",
        ),
        constraints={},
        host_capabilities=HostCapabilities(),
        host_context={"adapter": "cli"},
    )


def _make_executor(
    tmp_path: Path,
    *,
    fix_dispatcher: Any = None,
    continue_callback: Any = None,
    finalize_callback: Any = None,
    worker_factory: Any = None,
    build_allowed_paths: list[str] | None = None,
) -> AudisorOperationExecutor:
    """Build an executor with optional Fix routing.

    Uses temporary ArtifactStore, operation store, and mutation enforcer
    rooted under tmp_path so artifact persistence succeeds in tests.
    """
    store = AudisorOperationStore(tmp_path / "operations")
    artifact_store = ArtifactStore(tmp_path / "artifacts")
    enforcer = MutationEnforcer(base_dir=tmp_path)

    fix_route = None
    if fix_dispatcher is not None:
        fix_route = FixRouteConfig(
            fix_dispatcher=fix_dispatcher,
            continue_callback=continue_callback or (lambda op, result: result),
            finalize_callback=finalize_callback or (lambda op, result: result),
        )

    return AudisorOperationExecutor(
        config=ExecutorConfig(
            operation_store=store,
            artifact_store=artifact_store,
            mutation_enforcer=enforcer,
            fix_route=fix_route,
            worker_factory=worker_factory,
        )
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_fix_package_reaches_dispatcher_with_all_nested_values_preserved(tmp_path: Path):
    """A complete Fix package reaches AcceptedFixDispatcher with every nested value preserved."""
    captured: dict[str, Any] = {}

    class FakeDispatcher:
        def dispatch(self, operation, continue_impl, finalize_unresolved):
            captured["operation"] = operation
            captured["dispatch_calls"] = captured.get("dispatch_calls", 0) + 1
            return continue_impl(
                operation,
                {"status": "accepted", "handoff_path": "/tmp/handoff.json"},
            )

    executor = _make_executor(tmp_path, fix_dispatcher=FakeDispatcher())
    request = _make_fix_request()

    result = executor.execute(request)

    assert result.status == "accepted"
    op = captured["operation"]
    assert op.operation_id == "fix-op-1"

    # Findings preserved
    assert len(op.findings) == 1
    assert op.findings[0].id == "F-1"
    assert op.findings[0].type == "syntax"
    assert op.findings[0].file == "src/app.py"
    assert op.findings[0].severity == "high"
    assert op.findings[0].evidence == {"line": 42, "message": "undefined variable"}

    # Manifest preserved
    assert op.manifest.files == ["src/app.py"]
    assert op.manifest.dependency_closure == ["src/app.py", "src/utils.py"]
    assert op.manifest.input_hash == "abc123"
    assert op.manifest.file_hashes == {"src/app.py": "a" * 64, "src/utils.py": "b" * 64}

    # Statements preserved
    assert len(op.statements) == 3
    assert op.statements[0].type == "mutation_authority"
    assert op.statements[0].content == {"authorized": True, "scope": "repository"}

    # Plan preserved
    assert len(op.plan.steps) == 1
    assert op.plan.steps[0].id == "S-1"
    assert op.plan.steps[0].action == "repair"
    assert op.plan.steps[0].target_file == "src/app.py"
    assert op.plan.is_qualified is True

    # Workspace and authority preserved
    assert op.workspace_identity == {"path": "sandbox/fix-op-1", "root": "/repo"}
    assert op.authority_context == {"allowed_paths": ["src/app.py"], "scope": "repository"}


def test_dispatcher_invoked_exactly_once(tmp_path: Path):
    """The Fix dispatcher is invoked exactly once per operation."""
    dispatch_count = 0

    class CountingDispatcher:
        def dispatch(self, operation, continue_impl, finalize_unresolved):
            nonlocal dispatch_count
            dispatch_count += 1
            return continue_impl(operation, {"status": "accepted"})

    executor = _make_executor(tmp_path, fix_dispatcher=CountingDispatcher())
    request = _make_fix_request()

    executor.execute(request)
    assert dispatch_count == 1


def test_local_worker_not_constructed_for_fix(tmp_path: Path):
    """The generic LocalWorker is not constructed when a Fix operation is routed."""
    worker_constructed = False

    def failing_worker_factory(config):
        nonlocal worker_constructed
        worker_constructed = True
        raise AssertionError("LocalWorker should not be constructed for Fix")

    class FakeDispatcher:
        def dispatch(self, operation, continue_impl, finalize_unresolved):
            return continue_impl(operation, {"status": "accepted"})

    executor = _make_executor(
        tmp_path,
        fix_dispatcher=FakeDispatcher(),
        worker_factory=failing_worker_factory,
    )
    request = _make_fix_request()

    result = executor.execute(request)
    assert result.status == "accepted"
    assert worker_constructed is False


def test_accepted_fix_exposes_handoff_reference(tmp_path: Path):
    """An accepted Fix exposes its qualified handoff reference in artifacts."""
    handoff_path = str(tmp_path / "fix-op-1" / "qualified-fix-handoff.json")

    class FakeDispatcher:
        def dispatch(self, operation, continue_impl, finalize_unresolved):
            return continue_impl(
                operation,
                {"status": "accepted", "handoff_path": handoff_path},
            )

    executor = _make_executor(tmp_path, fix_dispatcher=FakeDispatcher())
    request = _make_fix_request()

    result = executor.execute(request)

    assert result.status == "accepted"
    assert result.execution is not None
    assert result.execution["handoff_path"] == handoff_path
    assert result.execution["fix_dispatched"] is True

    # Handoff reference in artifacts
    assert len(result.artifacts) == 1
    assert result.artifacts[0]["artifact_id"] == "qualified-fix-handoff"
    assert result.artifacts[0]["reference"] == handoff_path
    assert result.artifacts[0]["artifact_type"] == "handoff"


def test_unresolved_fix_blocks_continuation(tmp_path: Path):
    """An unresolved Fix blocks continuation and returns blocked status."""
    class FakeDispatcher:
        def dispatch(self, operation, continue_impl, finalize_unresolved):
            return finalize_unresolved(
                operation,
                {
                    "status": "validation_failed",
                    "error": {"code": "scoped_snapshot_required", "message": "manifest missing file hashes"},
                },
            )

    executor = _make_executor(tmp_path, fix_dispatcher=FakeDispatcher())
    request = _make_fix_request()

    result = executor.execute(request)

    assert result.status == "blocked"
    assert result.error is not None
    assert result.error.error_code.code == "scoped_snapshot_required"
    assert result.execution is not None
    assert result.execution["fix_dispatched"] is True


def test_build_still_uses_existing_execution_path(tmp_path: Path):
    """Build operations still use the existing LocalWorker execution path.

    Proves:
    - Build still invokes the worker
    - Build does not invoke the Fix dispatcher
    - Build completes successfully
    - Build produces its expected artifact/result shape
    """
    worker_called = False
    fix_dispatcher_called = False

    class FakeWorker:
        model_id = "test-model"

        def execute(self, task):
            nonlocal worker_called
            worker_called = True
            from audisor.schemas.task_output import TaskOutput
            output = TaskOutput(
                task_id=task.task_id,
                answer='[{"type": "build", "content": "ok"}]',
            )
            output.set_response_metadata(
                http_status=200,
                transport_succeeded=True,
                finish_reason="stop",
                tool_call_present=False,
                choice_count=1,
            )
            return output

    def worker_factory(config):
        return FakeWorker()

    class AssertingDispatcher:
        def dispatch(self, operation, continue_impl, finalize_unresolved):
            nonlocal fix_dispatcher_called
            fix_dispatcher_called = True
            raise AssertionError("Fix dispatcher must not be called for Build")

    # Build should go through _execute_mutation, not _execute_fix.
    # A fix_route is configured only to prove the dispatcher is NOT called.
    executor = _make_executor(
        tmp_path,
        fix_dispatcher=AssertingDispatcher(),
        worker_factory=worker_factory,
    )
    request = _make_build_request()

    result = executor.execute(request)

    # Build must invoke the worker
    assert worker_called is True
    # Build must NOT invoke the Fix dispatcher
    assert fix_dispatcher_called is False
    # Build must complete successfully — no "failed" alternative accepted
    assert result.status == "completed"
    # Build must produce its expected artifact/result shape
    assert result.execution is not None
    assert result.execution["model_id"] == "test-model"
    assert result.execution["finish_reason"] == "stop"
    assert len(result.artifacts) == 1
    assert result.artifacts[0]["artifact_id"] == "execution-result"
    assert result.artifacts[0]["artifact_type"] == "report"


def test_duplicate_fix_operation_idempotent(tmp_path: Path):
    """Duplicate Fix operations are idempotent through the existing store."""
    dispatch_count = 0

    class FakeDispatcher:
        def dispatch(self, operation, continue_impl, finalize_unresolved):
            nonlocal dispatch_count
            dispatch_count += 1
            return continue_impl(
                operation,
                {"status": "accepted", "handoff_path": "/tmp/handoff.json"},
            )

    executor = _make_executor(tmp_path, fix_dispatcher=FakeDispatcher())
    request = _make_fix_request()

    first = executor.execute(request)
    second = executor.execute(request)

    assert first.status == "accepted"
    assert dispatch_count == 1  # Dispatcher called only once
    # Second call returns cached/replayed result
    assert second.idempotency_replay is True


def test_fix_without_fix_route_blocks_without_worker(tmp_path: Path):
    """When no fix_route is configured, Fix blocks without constructing a worker.

    Proves:
    - result.status == "blocked"
    - result.error code == "fix_route_unavailable"
    - worker_factory was not called
    - generic mutation execution was not called
    - no Fix dispatcher was called
    """
    worker_factory_called = False

    def asserting_worker_factory(config):
        nonlocal worker_factory_called
        worker_factory_called = True
        raise AssertionError("worker_factory must not be called when Fix route is missing")

    # No fix_route configured — Fix must block, not fall through to mutation.
    # Do NOT pass fix_dispatcher; that would create a fix_route.
    executor = _make_executor(
        tmp_path,
        worker_factory=asserting_worker_factory,
    )
    request = _make_fix_request()

    result = executor.execute(request)

    # Fix must block, not fail or complete
    assert result.status == "blocked"
    # Error code must be the configuration/contract error
    assert result.error is not None
    assert result.error.error_code.code == "fix_route_unavailable"
    # worker_factory must not be called
    assert worker_factory_called is False
    # No mutation-completed result may be persisted
    assert result.execution is None or result.execution.get("fix_dispatched") is not True


def test_fix_invalid_payload_returns_blocked(tmp_path: Path):
    """An invalid Fix payload returns a blocked result."""
    class FakeDispatcher:
        def dispatch(self, operation, continue_impl, finalize_unresolved):
            raise AssertionError("Dispatcher should not be called for invalid payload")

    executor = _make_executor(tmp_path, fix_dispatcher=FakeDispatcher())

    # Build a request with an invalid fix payload (missing required fields)
    request = AudisorOperationRequest(
        operation_id="fix-bad",
        mode="fix",
        request={"fix": {"operation_id": "fix-bad"}},  # Missing findings, manifest, etc.
        authority=AuthorityContext(
            source=AuthoritySource(source_type="user", grant_id="test", host_identity="cli"),
            permissions=PermissionSet(
                allowed_paths=["."],
                prohibited_paths=[".git", ".codex"],
                allowed_tools=[],
                prohibited_tools=[],
            ),
            scope="repository",
        ),
        constraints={},
        host_capabilities=HostCapabilities(),
        host_context={"adapter": "cli"},
    )

    result = executor.execute(request)

    assert result.status == "blocked"
    assert result.error is not None
    assert result.error.error_code.code == "fix_contract_invalid"
