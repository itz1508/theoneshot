"""FastAPI proof for Phase 2A Builder preparation."""

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

from audisor.api.builds import get_build_preparer
from audisor.builder.preparer import BuildPreparer
from audisor.builder.store import BuildStore
from audisor.main import create_app
from provider_testkit import provider_router
from audisor.schemas.task_input import TaskInput

SCHEMA_ROOT = Path(__file__).resolve().parents[2] / "schemas"


class FakePlanningWorker:
    name = "fake-planner"

    def __init__(self, result: object) -> None:
        self.result = result
        self.calls: list[TaskInput] = []

    def execute(self, task: TaskInput) -> object:
        self.calls.append(task)
        return self.result


class NeverWorker:
    name = "never"

    def __init__(self) -> None:
        self.calls: list[TaskInput] = []

    def execute(self, task: TaskInput) -> object:
        self.calls.append(task)
        raise AssertionError("unselected worker was called")


def complete_prompt(objective: str, work: str, validation: str) -> str:
    return f"""## Objective
{objective}

## Inputs and repository paths
Use greeting.py and the repository test directory.

## Required work
{work}

## Ordered steps
1. Inspect the active Python package and test layout.
2. Implement the requested files.
3. Run the focused validation.

## Expected output
Return the completed implementation and tests.

## Validation
{validation}

## Evidence to return
Return changed paths and the exact test command output."""


def proof_plan(build_id: str = "builder-proof-001") -> dict[str, Any]:
    return {
        "build_id": build_id,
        "status": "ready",
        "gaps": [],
        "tasks": [
            {
                "task_id": "task-002",
                "title": "Add greeting tests",
                "depends_on": ["task-001"],
                "prompt": complete_prompt(
                    "Add tests for greet(name).",
                    "Add tests proving greet(name) returns Hello, <name>.",
                    "Run the greeting test module.",
                ),
                "expected_outputs": ["tests/test_greeting.py"],
                "validation": [
                    {
                        "argv": ["python", "-m", "pytest", "tests/test_greeting.py"],
                        "working_directory": ".",
                        "acceptable_exit_codes": [0],
                        "timeout_seconds": 60,
                    }
                ],
            },
            {
                "task_id": "task-001",
                "title": "Create greeting module",
                "depends_on": [],
                "prompt": complete_prompt(
                    "Create the greeting module.",
                    "Create greeting.py with greet(name).",
                    "Import greeting.py and exercise greet(name).",
                ),
                "expected_outputs": ["greeting.py"],
                "validation": [
                    {
                        "argv": ["python", "-m", "pytest", "tests/test_greeting.py"],
                        "working_directory": ".",
                        "acceptable_exit_codes": [0],
                        "timeout_seconds": 60,
                    }
                ],
            },
        ],
    }


def client_for(
    result: object,
    data_dir: Path,
    selected: str = "fireworks",
) -> tuple[TestClient, FakePlanningWorker, NeverWorker]:
    worker = FakePlanningWorker(result)
    other = NeverWorker()
    if selected == "fireworks":
        router = provider_router(selected, fireworks=worker, local=other)
    elif selected == "local-openai-compatible":
        router = provider_router(selected, fireworks=other, local=worker)
    else:
        router = provider_router(selected, fireworks=worker, local=other)
    preparer = BuildPreparer(router, BuildStore(data_dir))
    app = create_app()
    app.dependency_overrides[get_build_preparer] = lambda: preparer
    return TestClient(app), worker, other


def test_required_builder_proof_through_fastapi(tmp_path: Path) -> None:
    data_dir = tmp_path / "external-audisor-data"
    client, worker, other = client_for(json.dumps(proof_plan()), data_dir)
    request = {
        "build_id": "builder-proof-001",
        "instruction": (
            "Create a Python module named greeting.py containing a greet(name) "
            "function and add tests proving it returns 'Hello, <name>'."
        ),
    }
    response = client.post("/v1/builds/prepare", json=request)

    assert response.status_code == 200
    body = response.json()
    assert body["build_id"] == "builder-proof-001"
    assert body["status"] == "ready"
    assert body["gaps"] == []
    assert [task["task_id"] for task in body["tasks"]] == ["task-001", "task-002"]
    assert body["tasks"][1]["depends_on"] == ["task-001"]
    assert len({task["task_id"] for task in body["tasks"]}) == 2
    assert worker.calls and request["instruction"] in worker.calls[0].prompt
    assert other.calls == []

    build_root = data_dir / "builds" / "builder-proof-001"
    assert json.loads((build_root / "instruction.json").read_text(encoding="utf-8")) == {**request, "execution_context": None}
    assert json.loads((build_root / "plan.json").read_text(encoding="utf-8")) == body
    skill_files = sorted((build_root / "skills").glob("*/SKILL.md"))
    assert len(skill_files) == len(body["tasks"])
    mappings = [
        {
            "task_id": task["task_id"],
            "prompt": next(
                path.read_text(encoding="utf-8")
                for path in skill_files
                if path.parent.name.startswith(task["task_id"] + "-")
            ),
        }
        for task in body["tasks"]
    ]
    schema = json.loads(
        (SCHEMA_ROOT / "task-skill.schema.json").read_text(encoding="utf-8")
    )
    for mapping in mappings:
        Draft202012Validator(schema).validate(mapping)
        assert set(mapping) == {"task_id", "prompt"}
        for heading in (
            "Objective",
            "Inputs and repository paths",
            "Required work",
            "Ordered steps",
            "Expected output",
            "Validation",
            "Evidence to return",
        ):
            assert f"## {heading}" in mapping["prompt"]
    assert all(".agents" not in path.parts for path in skill_files)


def test_blocked_plan_returns_http_200_and_persists_without_skills(
    tmp_path: Path,
) -> None:
    result = json.dumps(
        {
            "build_id": "blocked-001",
            "status": "blocked",
            "gaps": ["The requested target repository path is missing."],
            "tasks": [],
        }
    )
    client, worker, other = client_for(result, tmp_path / "data")
    response = client.post(
        "/v1/builds/prepare",
        json={"build_id": "blocked-001", "instruction": "Prepare this build."},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "blocked"
    assert worker.calls
    assert other.calls == []
    build_root = tmp_path / "data" / "builds" / "blocked-001"
    assert (build_root / "instruction.json").is_file()
    assert (build_root / "plan.json").is_file()
    assert list((build_root / "skills").iterdir()) == []


def test_invalid_api_input_returns_422_without_worker_or_storage(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    client, worker, other = client_for(json.dumps(proof_plan()), data_dir)
    response = client.post(
        "/v1/builds/prepare",
        json={"build_id": "../escape", "instruction": "work"},
    )

    assert response.status_code == 422
    assert worker.calls == []
    assert other.calls == []
    assert not data_dir.exists()


def test_invalid_router_selection_returns_503_before_provider_or_storage(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    client, worker, other = client_for(
        json.dumps(proof_plan()),
        data_dir,
        selected="unsupported",
    )
    response = client.post(
        "/v1/builds/prepare",
        json={"build_id": "build-001", "instruction": "Prepare the build."},
    )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "provider_configuration_error"
    error_schema = json.loads(
        (SCHEMA_ROOT / "error.schema.json").read_text(encoding="utf-8")
    )
    Draft202012Validator(error_schema).validate(response.json())
    assert worker.calls == []
    assert other.calls == []
    assert not data_dir.exists()


def test_malformed_planner_json_returns_502_and_no_partial_build(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    client, worker, _other = client_for("not JSON", data_dir)
    response = client.post(
        "/v1/builds/prepare",
        json={"build_id": "build-001", "instruction": "Prepare the build."},
    )

    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "provider_invalid_response"
    error_schema = json.loads(
        (SCHEMA_ROOT / "error.schema.json").read_text(encoding="utf-8")
    )
    Draft202012Validator(error_schema).validate(response.json())
    assert len(worker.calls) == 1
    assert not (data_dir / "builds" / "build-001").exists()


def test_existing_build_returns_409_without_overwrite(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    client, _worker, _other = client_for(json.dumps(proof_plan("build-001")), data_dir)
    request = {"build_id": "build-001", "instruction": "Prepare the build."}
    first = client.post("/v1/builds/prepare", json=request)
    plan_path = data_dir / "builds" / "build-001" / "plan.json"
    before = plan_path.read_bytes()
    second = client.post("/v1/builds/prepare", json=request)

    assert first.status_code == 200
    assert second.status_code == 409
    assert plan_path.read_bytes() == before


def test_storage_failure_returns_generic_500_without_partial_build(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = tmp_path / "data"
    client, _worker, _other = client_for(json.dumps(proof_plan("build-001")), data_dir)

    def failing_publish(self: BuildStore, source: Path, destination: Path) -> None:
        raise OSError("sensitive path details")

    monkeypatch.setattr(BuildStore, "_publish_directory", failing_publish)
    response = client.post(
        "/v1/builds/prepare",
        json={"build_id": "build-001", "instruction": "Prepare the build."},
    )

    assert response.status_code == 500
    assert response.json() == {
        "detail": {
            "code": "build_storage_error",
            "message": "Build storage failed",
        }
    }
    assert "sensitive path details" not in response.text
    assert not (data_dir / "builds" / "build-001").exists()
