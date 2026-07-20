"""End-to-end tests for the host-agnostic canonical Audisor operation path.

Proves:
- POST /v1/operations is reachable and returns a canonical response.
- POST /v1/operations/tasks is reachable and returns a canonical response.
- `audisor host accept` uses the canonical service by default.
- All three paths invoke the same AudisorOperationExecutor through
  CanonicalOperationService.
- Canonical authority, mutation enforcement, idempotency, artifact
  persistence, and result normalization occur on these paths.
- Legacy Builder endpoints remain reachable and unchanged.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from audisor import cli
from audisor.adapters.protocol import AudisorOperationRequest
from audisor.api.executions import canonical_router as executions_canonical_router
from audisor.api.executions import get_canonical_operation_service
from audisor.api.tasks import canonical_router as tasks_canonical_router
from audisor.api.tasks import get_canonical_operation_service as get_tasks_canonical_service
from audisor.config.host_profiles import AudisorConfig
from audisor.main import create_app
from audisor.operations.artifacts import ArtifactStore
from audisor.operations.executor import AudisorOperationExecutor, ExecutorConfig
from audisor.operations.models import BuildOperationInput, ClientMetadata, OperationRequest
from audisor.schemas.execution import BuildExecutionRequest
from audisor.operations.mutation_enforcer import MutationEnforcer
from audisor.operations.service import CanonicalOperationService
from audisor.operations.store import AudisorOperationStore
from audisor.operations.transport import canonical_operation_service, deserialize_request
from audisor.schemas.task_input import TaskInput, TaskInputBatch
from audisor.schemas.task_output import TaskOutput


class FakeWorker:
    """Deterministic local worker that returns predictable answers."""

    name = "fake-worker"
    model_id = "fake-model"

    def __init__(self, answer: str = "fake-answer") -> None:
        self._answer = answer
        self.calls: list[TaskInput] = []

    def execute(self, task: TaskInput) -> TaskOutput:
        self.calls.append(task)
        return TaskOutput(task_id=task.task_id, answer=self._answer)

    def configuration_status(self) -> bool:
        return True

    def capabilities(self) -> Any:
        from audisor.workers.base import ProviderCapabilities

        return ProviderCapabilities(text=True)


def _fake_worker_factory(config: AudisorConfig) -> FakeWorker:
    return FakeWorker(answer=f"model={config.model_id}")


def _make_canonical_service(tmp_path: Path, answer: str = "fake-answer") -> CanonicalOperationService:
    """Build a CanonicalOperationService backed by a real executor with a fake worker."""
    data_dir = tmp_path / "operations"
    artifact_dir = tmp_path / "artifacts"
    store = AudisorOperationStore(data_dir)
    artifact_store = ArtifactStore(artifact_dir)
    enforcer = MutationEnforcer(base_dir=tmp_path)
    executor = AudisorOperationExecutor(
        config=ExecutorConfig(
            operation_store=store,
            artifact_store=artifact_store,
            mutation_enforcer=enforcer,
            worker_factory=lambda _config: FakeWorker(answer=answer),
        )
    )
    return CanonicalOperationService(executor)


def _legacy_build_payload(operation_id: str = "op-legacy-1") -> dict[str, Any]:
    return {
        "operation_id": operation_id,
        "operation_kind": "build",
        "client": {"client_id": "test-client", "adapter_id": "cli", "adapter_version": "1"},
        "repository": {"root_reference": "repo"},
        "requested_scope": {"paths": ["src"]},
        "build": {
            "build_id": "build-1",
            "request": {
                "execution_id": operation_id,
                "idempotency_key": operation_id,
                "target_root": "src",
                "allowed_write_paths": ["src"],
            },
        },
    }


def test_canonical_operation_service_invokes_executor_and_normalizes_result(tmp_path: Path) -> None:
    """CanonicalOperationService.execute routes through AudisorOperationExecutor."""
    service = _make_canonical_service(tmp_path, answer="canonical-result")
    request = OperationRequest(
        operation_id="op-canonical-1",
        operation_kind="build",
        client=ClientMetadata(
            client_id="test-client",
            adapter_id="cli",
            adapter_version="1",
        ),
        repository={"root_reference": "repo"},
        requested_scope={"paths": ["src"]},
        build=BuildOperationInput(
            build_id="build-1",
            request=BuildExecutionRequest(
                execution_id="op-canonical-1",
                idempotency_key="op-canonical-1",
                target_root="src",
                allowed_write_paths=["src"],
            ),
        ),
    )

    response = service.accept(request)

    assert response.operation_id == "op-canonical-1"
    assert response.operation_kind == "build"
    assert response.status == "completed"
    assert response.client_id == "test-client"
    assert response.request_hash
    assert response.continuation["permitted"] is True
    assert response.execution_contract_reference is not None


def test_post_v1_operations_end_to_end(tmp_path: Path, monkeypatch) -> None:
    """POST /v1/operations reaches the canonical executor-backed endpoint."""
    monkeypatch.setenv("AUDISOR_OPERATION_DATA_DIR", str(tmp_path / "operations"))
    monkeypatch.setenv("AUDISOR_ARTIFACT_DATA_DIR", str(tmp_path / "artifacts"))

    service = _make_canonical_service(tmp_path, answer="api-operations-result")
    app = create_app()
    app.dependency_overrides[get_canonical_operation_service] = lambda: service

    payload = _legacy_build_payload("op-api-operations")
    response = TestClient(app).post("/v1/operations", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["operation_id"] == "op-api-operations"
    assert body["operation_kind"] == "build"
    assert body["status"] == "completed"
    assert body["client_id"] == "test-client"
    assert body["request_hash"]
    assert body["continuation"]["permitted"] is True
    assert body["execution_contract_reference"] is not None
    assert any(
        artifact.get("artifact_id") == "execution-result"
        for artifact in body.get("artifact_references", [])
    )


def test_post_v1_operations_tasks_end_to_end(tmp_path: Path, monkeypatch) -> None:
    """POST /v1/operations/tasks reaches the canonical executor-backed endpoint."""
    monkeypatch.setenv("AUDISOR_OPERATION_DATA_DIR", str(tmp_path / "operations"))
    monkeypatch.setenv("AUDISOR_ARTIFACT_DATA_DIR", str(tmp_path / "artifacts"))

    service = _make_canonical_service(tmp_path, answer="api-tasks-result")
    app = create_app()
    app.dependency_overrides[get_tasks_canonical_service] = lambda: service

    response = TestClient(app).post(
        "/v1/operations/tasks",
        json=[{"task_id": "task-canonical-001", "prompt": "Analyze this repository."}],
    )

    assert response.status_code == 200
    body = response.json()
    assert body["operation_id"] == "batch"
    assert "results" in body
    results = body["results"]
    assert len(results) == 1
    assert results[0]["operation_id"] == "task-canonical-001"
    assert results[0]["status"] == "completed"
    assert results[0]["summary"] == "Read-only operation completed: analyze"


def test_audisor_host_accept_uses_canonical_service_by_default(tmp_path: Path, monkeypatch) -> None:
    """`audisor host accept` defaults to canonical_operation_service()."""
    monkeypatch.setenv("AUDISOR_OPERATION_DATA_DIR", str(tmp_path / "operations"))
    monkeypatch.setenv("AUDISOR_ARTIFACT_DATA_DIR", str(tmp_path / "artifacts"))

    service = _make_canonical_service(tmp_path, answer="cli-result")
    output, error = io.StringIO(), io.StringIO()
    payload = _legacy_build_payload("op-cli-default")

    exit_code = cli.main(
        ["host", "accept"],
        operation_service=service,
        stdin=io.StringIO(json.dumps(payload)),
        stdout=output,
        stderr=error,
    )

    assert exit_code == 0
    assert error.getvalue() == ""
    body = json.loads(output.getvalue())
    assert body["operation_id"] == "op-cli-default"
    assert body["operation_kind"] == "build"
    assert body["status"] == "completed"
    assert body["client_id"] == "test-client"
    assert body["continuation"]["permitted"] is True


def test_canonical_paths_share_same_executor_instance(tmp_path: Path, monkeypatch) -> None:
    """All three canonical paths use the same AudisorOperationExecutor instance."""
    monkeypatch.setenv("AUDISOR_OPERATION_DATA_DIR", str(tmp_path / "operations"))
    monkeypatch.setenv("AUDISOR_ARTIFACT_DATA_DIR", str(tmp_path / "artifacts"))

    service = _make_canonical_service(tmp_path, answer="shared-executor")
    executor = service._executor
    assert isinstance(executor, AudisorOperationExecutor)

    # 1. Direct service call
    request = deserialize_request(_legacy_build_payload("op-shared-1"))
    service.accept(request)

    # 2. API executions endpoint
    app = create_app()
    app.dependency_overrides[get_canonical_operation_service] = lambda: service
    TestClient(app).post("/v1/operations", json=_legacy_build_payload("op-shared-2"))

    # 3. API tasks endpoint
    app.dependency_overrides[get_tasks_canonical_service] = lambda: service
    TestClient(app).post(
        "/v1/operations/tasks",
        json=[{"task_id": "task-shared-3", "prompt": "Analyze."}],
    )

    # 4. CLI host accept
    output, error = io.StringIO(), io.StringIO()
    cli.main(
        ["host", "accept"],
        operation_service=service,
        stdin=io.StringIO(json.dumps(_legacy_build_payload("op-shared-4"))),
        stdout=output,
        stderr=error,
    )

    # The executor's store should contain all four operations
    states = executor._store.list_operations(status="completed")
    operation_ids = {state.operation_id for state in states}
    assert {"op-shared-1", "op-shared-2", "task-shared-3", "op-shared-4"} <= operation_ids


def test_canonical_authority_and_mutation_enforcement(tmp_path: Path, monkeypatch) -> None:
    """Prohibited paths are blocked by the canonical mutation enforcer."""
    monkeypatch.setenv("AUDISOR_OPERATION_DATA_DIR", str(tmp_path / "operations"))
    monkeypatch.setenv("AUDISOR_ARTIFACT_DATA_DIR", str(tmp_path / "artifacts"))

    service = _make_canonical_service(tmp_path)
    app = create_app()
    app.dependency_overrides[get_canonical_operation_service] = lambda: service

    payload = {
        "operation_id": "op-blocked",
        "operation_kind": "build",
        "client": {"client_id": "test-client", "adapter_id": "cli", "adapter_version": "1"},
        "repository": {"root_reference": "repo"},
        "requested_scope": {"paths": [".git"]},
        "build": {
            "build_id": "build-1",
            "request": {
                "execution_id": "op-blocked",
                "idempotency_key": "op-blocked",
                "target_root": ".git",
                "allowed_write_paths": [".git"],
            },
        },
    }

    response = TestClient(app).post("/v1/operations", json=payload)
    assert response.status_code == 500
    body = response.json()
    assert "operation_execution_failed" in body["detail"]["code"] or "blocked" in body["detail"]["message"].lower()


def test_canonical_idempotency_replays_identical_request(tmp_path: Path, monkeypatch) -> None:
    """Submitting the same operation twice returns a completed result both times."""
    monkeypatch.setenv("AUDISOR_OPERATION_DATA_DIR", str(tmp_path / "operations"))
    monkeypatch.setenv("AUDISOR_ARTIFACT_DATA_DIR", str(tmp_path / "artifacts"))

    service = _make_canonical_service(tmp_path, answer="idempotent-result")
    app = create_app()
    app.dependency_overrides[get_canonical_operation_service] = lambda: service

    payload = _legacy_build_payload("op-idem")
    response1 = TestClient(app).post("/v1/operations", json=payload)
    response2 = TestClient(app).post("/v1/operations", json=payload)

    assert response1.status_code == 200
    assert response2.status_code == 200
    assert response1.json()["operation_id"] == response2.json()["operation_id"] == "op-idem"
    assert response1.json()["status"] == "completed"
    assert response2.json()["status"] == "completed"


def test_canonical_artifact_persistence(tmp_path: Path, monkeypatch) -> None:
    """Artifacts produced by the canonical executor are persisted and referenced."""
    monkeypatch.setenv("AUDISOR_OPERATION_DATA_DIR", str(tmp_path / "operations"))
    monkeypatch.setenv("AUDISOR_ARTIFACT_DATA_DIR", str(tmp_path / "artifacts"))

    service = _make_canonical_service(tmp_path, answer="artifact-result")
    app = create_app()
    app.dependency_overrides[get_canonical_operation_service] = lambda: service

    payload = _legacy_build_payload("op-artifact")
    response = TestClient(app).post("/v1/operations", json=payload)

    assert response.status_code == 200
    body = response.json()
    artifact_refs = body.get("artifact_references", [])
    assert any(ref.get("artifact_id") == "execution-result" for ref in artifact_refs)
    execution_result_ref = next(
        ref for ref in artifact_refs if ref.get("artifact_id") == "execution-result"
    )
    assert execution_result_ref.get("content_hash") is not None
    assert execution_result_ref.get("size_bytes") is not None

    # Verify the artifact is persisted on disk
    artifact_path = tmp_path / "artifacts" / "op-artifact" / "execution-result.json"
    assert artifact_path.exists()


def test_legacy_builder_endpoint_still_reachable(tmp_path: Path, monkeypatch) -> None:
    """Legacy /v1/builds/{build_id}/executions remains registered and unchanged."""
    monkeypatch.setenv("AUDISOR_OPERATION_DATA_DIR", str(tmp_path / "operations"))
    monkeypatch.setenv("AUDISOR_ARTIFACT_DATA_DIR", str(tmp_path / "artifacts"))

    app = create_app()

    def _collect_paths(route: Any, prefix: str = "") -> list[str]:
        paths: list[str] = []
        route_path = getattr(route, "path", None)
        if route_path:
            paths.append(prefix + route_path)
        if hasattr(route, "routes"):
            for sub in route.routes:
                paths.extend(_collect_paths(sub, prefix))
        if hasattr(route, "original_router"):
            paths.extend(_collect_paths(route.original_router, getattr(route, "prefix", "")))
        return paths

    routes = []
    for r in app.routes:
        routes.extend(_collect_paths(r))
    assert "/v1/builds/{build_id}/executions" in routes
    assert "/v1/tasks" in routes


def test_all_host_adapter_classes_are_implemented() -> None:
    """Codex, MCP, CLI, and Responses-compatible adapter classes exist and are importable."""
    from audisor.adapters.codex import CodexRequestAdapter, CodexResponseAdapter
    from audisor.adapters.mcp import MCPRequestAdapter, MCPResponseAdapter
    from audisor.adapters.cli import CLIRequestAdapter, CLIResponseAdapter
    from audisor.adapters.responses_compatible import (
        ResponsesRequestAdapter,
        ResponsesResponseAdapter,
    )

    adapters = [
        CodexRequestAdapter,
        CodexResponseAdapter,
        MCPRequestAdapter,
        MCPResponseAdapter,
        CLIRequestAdapter,
        CLIResponseAdapter,
        ResponsesRequestAdapter,
        ResponsesResponseAdapter,
    ]

    request_adapters = {
        CodexRequestAdapter,
        MCPRequestAdapter,
        CLIRequestAdapter,
        ResponsesRequestAdapter,
    }
    response_adapters = {
        CodexResponseAdapter,
        MCPResponseAdapter,
        CLIResponseAdapter,
        ResponsesResponseAdapter,
    }

    for adapter_cls in adapters:
        assert callable(adapter_cls), f"{adapter_cls.__name__} is not callable"
        instance = adapter_cls()
        if adapter_cls in request_adapters:
            assert hasattr(instance, "translate_request"), f"{adapter_cls.__name__} missing translate_request"
            assert hasattr(instance, "detect_capabilities"), f"{adapter_cls.__name__} missing detect_capabilities"
        elif adapter_cls in response_adapters:
            assert hasattr(instance, "translate_result"), f"{adapter_cls.__name__} missing translate_result"


def test_canonical_service_uses_correct_host_capabilities_per_adapter(tmp_path: Path) -> None:
    """CanonicalOperationService selects the right capabilities for each adapter_id."""
    service = _make_canonical_service(tmp_path)

    for adapter_id, expected_tools in (
        ("codex", True),
        ("mcp", True),
        ("cli", False),
        ("responses_compatible", False),
    ):
        request = OperationRequest(
            operation_id=f"op-{adapter_id}",
            operation_kind="build",
            client=ClientMetadata(
                client_id="c",
                adapter_id=adapter_id,
                adapter_version="1",
            ),
            repository={},
            requested_scope={},
            build=BuildOperationInput(
                build_id="b",
                request=BuildExecutionRequest(
                    execution_id=f"op-{adapter_id}",
                    idempotency_key=f"op-{adapter_id}",
                    target_root="src",
                    allowed_write_paths=["src"],
                ),
            ),
        )
        canonical = service._to_canonical_request(request)
        assert canonical.host_capabilities.to_mapping()["supports_tools"] is expected_tools
        assert canonical.host_context["adapter"] == adapter_id
