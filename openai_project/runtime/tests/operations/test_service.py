from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from audisor.operations.models import BuildOperationInput, ClientMetadata, FixOperationInput, OperationRequest
from audisor.operations.service import AcceptedOperationService, CanonicalOperationService
from audisor.operations.store import SharedOperationStore
from audisor.schemas.execution import BuildExecutionRequest


class Host:
    def __init__(self):
        self.calls = 0

    def execute(self, build_id, request):
        self.calls += 1
        return {"status": "accepted", "decision_state": "no_material_gap", "authority_limits": {"apply": False}}


def request():
    host_request = BuildExecutionRequest(execution_id="op-1", idempotency_key="op-1", target_root="C:/target", allowed_write_paths=["src"])
    return OperationRequest("op-1", "build", ClientMetadata("client", "adapter", "1.0"), {"root_reference": "repo"}, {"paths": ["src"]}, BuildOperationInput("build-1", host_request))


def test_service_routes_once_and_duplicate_returns_existing(tmp_path):
    host = Host()
    service = AcceptedOperationService(SharedOperationStore(tmp_path), build_executor=host, fix_dispatcher=object(), fix_continue=lambda *_: None, fix_finalize=lambda *_: None)
    first = service.accept(request())
    second = service.accept(request())
    assert first.status == "accepted"
    assert second.status == "existing"
    assert second.existing_result is True
    assert host.calls == 1
    stored = (tmp_path / "op-1.json").read_text()
    assert '"adapter_id": "adapter"' in stored
    assert '"apply": false' in stored


def test_service_normalizes_host_failure_without_ignite_ownership(tmp_path):
    class FailingHost:
        def execute(self, *_):
            raise RuntimeError("host unavailable")

    service = AcceptedOperationService(SharedOperationStore(tmp_path), build_executor=FailingHost(), fix_dispatcher=object(), fix_continue=lambda *_: None, fix_finalize=lambda *_: None)
    response = service.accept(request())
    assert response.status == "failed"
    assert response.continuation["permitted"] is False


def test_service_routes_fix_once(tmp_path):
    operation = SimpleNamespace(operation_id="op-fix", findings=["finding"])
    calls = []
    dispatcher = type("Dispatcher", (), {"dispatch": lambda self, op, cont, final: calls.append(op.operation_id) or {"status": "blocked", "decision_state": "material_gap_found"}})()
    request_value = OperationRequest("op-fix", "fix", ClientMetadata("client", "adapter", "1"), {}, {}, fix=FixOperationInput(operation))
    service = AcceptedOperationService(SharedOperationStore(tmp_path), build_executor=object(), fix_dispatcher=dispatcher, fix_continue=lambda *_: None, fix_finalize=lambda *_: None)
    response = service.accept(request_value)
    assert response.status == "blocked"
    assert response.continuation["permitted"] is False
    assert calls == ["op-fix"]


# --- CanonicalOperationService Fix serialization regression tests ---


@dataclass(frozen=True)
class FakeFinding:
    id: str
    type: str
    file: str
    severity: str
    evidence: dict[str, Any]


@dataclass(frozen=True)
class FakeManifest:
    files: list[str]
    dependency_closure: list[str]
    input_hash: str
    file_hashes: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class FakeStatement:
    type: str
    content: dict[str, Any]
    findings_ref_hash: str
    manifest_ref_hash: str


@dataclass(frozen=True)
class FakePlanStep:
    id: str
    action: str
    target_file: str
    originating_finding_id: str
    acceptance_criterion: str | None


@dataclass(frozen=True)
class FakePlan:
    steps: list[FakePlanStep]
    target_files: list[str]
    is_qualified: bool


@dataclass(frozen=True)
class FakeFixOperation:
    operation_id: str
    findings: list[FakeFinding]
    manifest: FakeManifest
    statements: list[FakeStatement]
    plan: FakePlan
    workspace_identity: dict[str, Any]
    authority_context: dict[str, Any]
    aflow_analysis_request: dict[str, Any] | None = None


class FakeCanonicalExecutor:
    """Captures the canonical request for inspection."""

    def __init__(self) -> None:
        self.captured_request = None

    def execute(self, request: Any) -> Any:
        self.captured_request = request
        from audisor.operations.result import AudisorOperationResult
        return AudisorOperationResult(
            operation_id=request.operation_id,
            status="accepted",
            execution={"receipt_id": "test-receipt"},
            artifacts=[],
            error=None,
        )


def _make_fix_request() -> OperationRequest:
    """Create a Fix operation request with complete payload."""
    finding = FakeFinding(
        id="F-1",
        type="syntax",
        file="src/app.py",
        severity="high",
        evidence={"line": 42, "message": "undefined variable"},
    )
    manifest = FakeManifest(
        files=["src/app.py"],
        dependency_closure=["src/app.py", "src/utils.py"],
        input_hash="abc123",
        file_hashes={"src/app.py": "a" * 64, "src/utils.py": "b" * 64},
    )
    statement = FakeStatement(
        type="mutation_authority",
        content={"authorized": True, "scope": "repository"},
        findings_ref_hash="findings-hash",
        manifest_ref_hash="manifest-hash",
    )
    plan_step = FakePlanStep(
        id="S-1",
        action="repair",
        target_file="src/app.py",
        originating_finding_id="F-1",
        acceptance_criterion="test passes",
    )
    plan = FakePlan(
        steps=[plan_step],
        target_files=["src/app.py"],
        is_qualified=True,
    )
    operation = FakeFixOperation(
        operation_id="fix-op-1",
        findings=[finding],
        manifest=manifest,
        statements=[statement],
        plan=plan,
        workspace_identity={"path": "sandbox/fix-op-1", "root": "/repo"},
        authority_context={"allowed_paths": ["src/app.py"], "scope": "repository"},
        aflow_analysis_request={"provider": "local", "model": "test"},
    )
    return OperationRequest(
        operation_id="fix-op-1",
        operation_kind="fix",
        client=ClientMetadata("client-1", "cli", "1.0.0"),
        repository={"root_reference": "/repo"},
        requested_scope={"paths": ["src"]},
        fix=FixOperationInput(operation),
    )


def test_canonical_service_preserves_complete_fix_payload():
    """Regression test: Fix operation must preserve complete accepted package.

    The canonical request must contain all Fix fields, not just operation_id.
    """
    executor = FakeCanonicalExecutor()
    service = CanonicalOperationService(executor)
    request = _make_fix_request()

    service.accept(request)

    assert executor.captured_request is not None
    canonical_request = executor.captured_request
    assert canonical_request.mode == "fix"

    fix_payload = canonical_request.request["fix"]

    # Verify operation_id is present
    assert fix_payload["operation_id"] == "fix-op-1"

    # Verify findings are preserved with nested values
    assert len(fix_payload["findings"]) == 1
    finding = fix_payload["findings"][0]
    assert finding["id"] == "F-1"
    assert finding["type"] == "syntax"
    assert finding["file"] == "src/app.py"
    assert finding["severity"] == "high"
    assert finding["evidence"] == {"line": 42, "message": "undefined variable"}

    # Verify manifest is preserved with nested values
    manifest = fix_payload["manifest"]
    assert manifest["files"] == ["src/app.py"]
    assert manifest["dependency_closure"] == ["src/app.py", "src/utils.py"]
    assert manifest["input_hash"] == "abc123"
    assert manifest["file_hashes"]["src/app.py"] == "a" * 64

    # Verify statements are preserved
    assert len(fix_payload["statements"]) == 1
    statement = fix_payload["statements"][0]
    assert statement["type"] == "mutation_authority"
    assert statement["content"] == {"authorized": True, "scope": "repository"}

    # Verify plan is preserved with nested steps
    plan = fix_payload["plan"]
    assert plan["steps"][0]["id"] == "S-1"
    assert plan["steps"][0]["action"] == "repair"
    assert plan["steps"][0]["target_file"] == "src/app.py"
    assert plan["is_qualified"] is True

    # Verify workspace_identity and authority_context
    assert fix_payload["workspace_identity"] == {"path": "sandbox/fix-op-1", "root": "/repo"}
    assert fix_payload["authority_context"] == {"allowed_paths": ["src/app.py"], "scope": "repository"}

    # Verify aflow_analysis_request compatibility field
    assert fix_payload["aflow_analysis_request"] == {"provider": "local", "model": "test"}


def test_canonical_service_fix_payload_not_reduced_to_operation_id():
    """Regression test: Fix payload must not be reduced to only operation_id."""
    executor = FakeCanonicalExecutor()
    service = CanonicalOperationService(executor)
    request = _make_fix_request()

    service.accept(request)

    fix_payload = executor.captured_request.request["fix"]

    # The defect was that fix payload was reduced to only operation_id
    # Verify that other fields exist and are not empty
    assert "findings" in fix_payload
    assert "manifest" in fix_payload
    assert "statements" in fix_payload
    assert "plan" in fix_payload
    assert "workspace_identity" in fix_payload
    assert "authority_context" in fix_payload

    # Verify they have actual content, not just empty placeholders
    assert len(fix_payload["findings"]) > 0
    assert fix_payload["manifest"] is not None
    assert len(fix_payload["statements"]) > 0
    assert fix_payload["plan"] is not None


def test_canonical_service_build_translation_unchanged():
    """Verify Build translation remains unchanged after Fix serialization fix."""
    executor = FakeCanonicalExecutor()
    service = CanonicalOperationService(executor)

    host_request = BuildExecutionRequest(
        execution_id="build-op-1",
        idempotency_key="build-op-1",
        target_root="C:/target",
        allowed_write_paths=["src"],
    )
    request = OperationRequest(
        operation_id="build-op-1",
        operation_kind="build",
        client=ClientMetadata("client-1", "cli", "1.0.0"),
        repository={"root_reference": "/repo"},
        requested_scope={"paths": ["src"]},
        build=BuildOperationInput("build-1", host_request),
    )

    service.accept(request)

    assert executor.captured_request is not None
    canonical_request = executor.captured_request
    assert canonical_request.mode == "build"

    # Verify build payload structure
    build_payload = canonical_request.request["build"]
    assert build_payload["build_id"] == "build-1"
    assert "request" in build_payload
    assert build_payload["request"]["execution_id"] == "build-op-1"

    # Verify fix is not present
    assert "fix" not in canonical_request.request
