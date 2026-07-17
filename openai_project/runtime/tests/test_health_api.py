"""Provider-neutral liveness, readiness, schema, and OpenAPI proofs."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

from audisor.api.health import get_provider_router
from audisor.main import create_app
from audisor.routing.registry import ProviderRegistry
from audisor.routing.router import ProviderRouter
from audisor.schemas.task_input import TaskInput
from audisor.schemas.task_output import TaskOutput
from audisor.workers.base import ProviderCapabilities

SCHEMAS = Path(__file__).resolve().parents[2] / "schemas"


class ReadyProvider:
    provider_id = "test-provider"

    def configuration_status(self) -> bool:
        return True

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(text=True, structured_output=True)

    def execute(self, task: TaskInput) -> TaskOutput:
        return TaskOutput(task_id=task.task_id, answer="ready")


def client_for(router: ProviderRouter) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_provider_router] = lambda: router
    return TestClient(app)


def validate_schema(name: str, instance: object) -> None:
    schema = json.loads((SCHEMAS / name).read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(instance)


def test_health_is_liveness_only_and_does_not_construct_provider() -> None:
    constructions: list[str] = []
    router = ProviderRouter(
        "test-provider",
        ProviderRegistry({"test-provider": lambda: constructions.append("called")}),
    )
    response = client_for(router).get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert constructions == []
    validate_schema("health.schema.json", response.json())


def test_missing_provider_selection_is_degraded_not_implicitly_defaulted(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("AUDISOR_DATA_DIR", str(tmp_path / "data"))
    response = client_for(ProviderRouter(None, ProviderRegistry())).get("/ready")
    assert response.status_code == 200
    assert response.json() == {
        "status": "degraded",
        "provider": {
            "selected": None,
            "configuration": "missing",
            "capabilities_loaded": False,
        },
        "data_root_ready": True,
        "schemas_ready": True,
    }
    validate_schema("readiness.schema.json", response.json())


def test_ready_reports_only_generic_provider_state(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AUDISOR_DATA_DIR", str(tmp_path / "data"))
    provider = ReadyProvider()
    response = client_for(
        ProviderRouter(provider.provider_id, ProviderRegistry({provider.provider_id: lambda: provider}))
    ).get("/ready")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "provider": {
            "selected": "test-provider",
            "configuration": "present",
            "capabilities_loaded": True,
        },
        "data_root_ready": True,
        "schemas_ready": True,
    }
    serialized = response.text.lower()
    assert "model" not in serialized
    assert "key" not in serialized
    validate_schema("readiness.schema.json", response.json())


def test_openapi_is_provider_neutral_and_preserves_required_routes() -> None:
    document = create_app().openapi()
    assert {
        "/health",
        "/ready",
        "/v1/tasks",
        "/v1/builds/prepare",
        "/v1/builds/{build_id}/executions",
    }.issubset(document["paths"])
    serialized = json.dumps(document, sort_keys=True).lower()
    for forbidden in (
        "fireworks",
        "local-openai-compatible",
        "fireworks_api_key",
        "fireworks_model",
        "local_model_id",
    ):
        assert forbidden not in serialized

