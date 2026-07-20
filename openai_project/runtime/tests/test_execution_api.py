"""HTTP contracts for secure prepared-build execution."""

import json
from pathlib import Path

from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, RefResolver

from audisor.api.executions import get_build_executor
from audisor.builder.authority import TargetAuthorityResolver
from audisor.builder.execution_store import ExecutionStore
from audisor.builder.execution_store import ExecutionStoreError
from audisor.builder.executor import BuildExecutor
from audisor.builder.skill_renderer import render_skills
from audisor.builder.store import BuildStore
from audisor.builder.task_loader import PreparedBuildLoader
from audisor.builder.task_loader import PreparedBuildNotFoundError
from audisor.main import app
from provider_testkit import provider_router
from audisor.schemas.build import BuildPlan, BuildRequest
from audisor.schemas.task_input import TaskInput
from audisor.workers.base import ProviderConfigurationError
from audisor.audisor_lifecycle.operation import FrozenAudisorPolicy


class Worker:
    name = "api-worker"

    def __init__(self) -> None:
        self.calls: list[TaskInput] = []

    def execute(self, task: TaskInput) -> str:
        self.calls.append(task)
        return json.dumps(
            {
                "summary": "Create greeting.",
                "mutations": [
                    {
                        "action_id": "mutation-001",
                        "type": "write_file",
                        "path": "src/greeting.py",
                        "content": "VALUE = 'ready'\n",
                    }
                ],
                "expected_changed_paths": ["src/greeting.py"],
            }
        )


class NeverWorker:
    name = "never"

    def execute(self, task: TaskInput):
        raise AssertionError(task.task_id)


class RaisingExecutor:
    def __init__(self, error: Exception) -> None:
        self.error = error

    def execute(self, _build_id: str, _request):
        raise self.error


def prompt() -> str:
    return """## Objective
Create greeting.

## Inputs and repository paths
Use the isolated workspace.

## Required work
Write src/greeting.py.

## Ordered steps
1. Write the file.

## Expected output
src/greeting.py exists.

## Validation
Record executable validation as deferred and verify the expected file statically.

## Evidence to return
Return hashes."""


def configured(tmp_path: Path) -> tuple[BuildExecutor, Path, Worker]:
    data = tmp_path / "data"
    store = BuildStore(data)
    plan = BuildPlan.model_validate(
        {
            "build_id": "api-build-001",
            "status": "ready",
            "gaps": [],
            "tasks": [
                {
                    "task_id": "task-001",
                    "title": "Create greeting",
                    "depends_on": [],
                    "prompt": prompt(),
                    "expected_outputs": ["src/greeting.py"],
                    "validation": [
                        {
                            "argv": ["python", "-m", "pytest", "tests/test_greeting.py", "-q"],
                            "working_directory": ".",
                            "acceptable_exit_codes": [0],
                            "timeout_seconds": 120,
                        }
                    ],
                }
            ],
        }
    )
    store.publish(
        BuildRequest(build_id=plan.build_id, instruction="Create greeting."),
        plan,
        render_skills(plan.build_id, plan.tasks),
    )
    target = tmp_path / "target"
    (target / "src").mkdir(parents=True)
    (target / "tests").mkdir()
    worker = Worker()
    executor = BuildExecutor(
        router=provider_router("fireworks", worker, NeverWorker()),
        loader=PreparedBuildLoader(store),
        authority=TargetAuthorityResolver(
            data_dir=data,
            product_root=tmp_path / "product",
            reference_roots=(tmp_path / "reference",),
            approved_target_roots=(tmp_path,),
        ),
        store=ExecutionStore(data_dir=data),
        aflow_policy_reader=lambda: FrozenAudisorPolicy(False, "local-openai-compatible", "qwen2.5-coder:7b", "http://127.0.0.1:11434"),
    )
    return executor, target, worker


def payload(target: Path) -> dict[str, object]:
    return {
        "execution_id": "execution-001",
        "idempotency_key": "request-001",
        "target_root": str(target),
        "allowed_write_paths": ["src"],
    }


def validate_declared_body(status: int, body: object) -> None:
    document = app.openapi()
    response = document["paths"]["/v1/builds/{build_id}/executions"]["post"]["responses"][str(status)]
    response_schema = response["content"]["application/json"]["schema"]
    Draft202012Validator(
        response_schema,
        resolver=RefResolver.from_schema(document),
    ).validate(body)


def test_execution_api_returns_manifest_bound_success(tmp_path: Path) -> None:
    executor, target, worker = configured(tmp_path)
    app.dependency_overrides[get_build_executor] = lambda: executor
    try:
        response = TestClient(app).post("/v1/builds/api-build-001/executions", json=payload(target))
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    validate_declared_body(200, response.json())
    assert response.json()["status"] == "completed"
    assert response.json()["terminal_manifest_sha256"]
    assert worker.calls[0].task_id == "task-001"


def test_provider_configuration_error_is_declared_503(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    app.dependency_overrides[get_build_executor] = lambda: RaisingExecutor(
        ProviderConfigurationError("AUDISOR_PROVIDER is not configured")
    )
    try:
        response = TestClient(app).post("/v1/builds/api-build-001/executions", json=payload(target))
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 503
    validate_declared_body(503, response.json())
    assert response.json() == {
        "detail": {
            "code": "provider_configuration_error",
            "message": "AUDISOR_PROVIDER is not configured",
        }
    }


def test_reused_idempotency_with_changed_request_returns_declared_409(tmp_path: Path) -> None:
    executor, target, _worker = configured(tmp_path)
    app.dependency_overrides[get_build_executor] = lambda: executor
    try:
        client = TestClient(app)
        assert client.post("/v1/builds/api-build-001/executions", json=payload(target)).status_code == 200
        changed = payload(target)
        changed["allowed_write_paths"] = ["tests"]
        response = client.post("/v1/builds/api-build-001/executions", json=changed)
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 409
    validate_declared_body(409, response.json())
    assert response.json()["detail"]["code"] == "execution_conflict"


def test_unsafe_execution_id_is_422(tmp_path: Path) -> None:
    executor, target, _worker = configured(tmp_path)
    app.dependency_overrides[get_build_executor] = lambda: executor
    try:
        request = payload(target)
        request["execution_id"] = "../escape"
        response = TestClient(app).post("/v1/builds/api-build-001/executions", json=request)
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 422
    validate_declared_body(422, response.json())


def test_missing_prepared_build_returns_declared_404() -> None:
    app.dependency_overrides[get_build_executor] = lambda: RaisingExecutor(
        PreparedBuildNotFoundError()
    )
    try:
        response = TestClient(app).post(
            "/v1/builds/missing-build/executions",
            json={
                "execution_id": "execution-001",
                "idempotency_key": "request-001",
                "target_root": "D:/target",
                "allowed_write_paths": ["src"],
            },
        )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 404
    validate_declared_body(404, response.json())
    assert response.json() == {
        "detail": {"code": "build_not_found", "message": "Prepared build not found"}
    }


def test_execution_store_failure_returns_declared_500() -> None:
    app.dependency_overrides[get_build_executor] = lambda: RaisingExecutor(
        ExecutionStoreError()
    )
    try:
        response = TestClient(app).post(
            "/v1/builds/build-001/executions",
            json={
                "execution_id": "execution-001",
                "idempotency_key": "request-001",
                "target_root": "D:/target",
                "allowed_write_paths": ["src"],
            },
        )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 500
    validate_declared_body(500, response.json())
    assert response.json() == {
        "detail": {
            "code": "execution_storage_error",
            "message": "Execution storage failed",
        }
    }


def test_cross_build_authority_collision_is_409_before_workspace_or_worker(tmp_path: Path) -> None:
    executor, target, worker = configured(tmp_path)
    executor.store.global_authority.acquire(
        build_id="other-build",
        execution_id="other-execution",
        idempotency_key="other-key",
        request_fingerprint="a" * 64,
        target_root=target,
        allowed_paths=[target / "src"],
    )
    app.dependency_overrides[get_build_executor] = lambda: executor
    try:
        response = TestClient(app).post(
            "/v1/builds/api-build-001/executions", json=payload(target)
        )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 409
    validate_declared_body(409, response.json())
    assert worker.calls == []
    execution = executor.loader.store.build_path("api-build-001") / "executions/execution-001"
    assert not execution.exists()


def test_openapi_declares_all_phase2b_response_schemas() -> None:
    operation = app.openapi()["paths"]["/v1/builds/{build_id}/executions"]["post"]
    for status in ("200", "404", "409", "422", "500", "502", "503"):
        assert "content" in operation["responses"][status]
        assert "schema" in operation["responses"][status]["content"]["application/json"]
