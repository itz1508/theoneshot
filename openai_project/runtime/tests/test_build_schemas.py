"""Phase 2A runtime and published JSON schema coverage."""

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, ValidationError as JsonSchemaError
from pydantic import ValidationError

from audisor.schemas.build import BuildPlan, BuildRequest, BuildTask, TaskSkill

SCHEMA_ROOT = Path(__file__).resolve().parents[2] / "schemas"


def complete_prompt(label: str = "the task") -> str:
    return f"""## Objective
Complete {label}.

## Inputs and repository paths
Use the current repository root and the files named by the build instruction.

## Required work
Implement the requested change completely.

## Ordered steps
1. Inspect the active implementation.
2. Apply the required change.

## Expected output
Return the completed files.

## Validation
Run focused tests for the change.

## Evidence to return
Return changed paths and test output."""


def ready_payload() -> dict:
    return {
        "build_id": "build-001",
        "status": "ready",
        "gaps": [],
        "tasks": [
            {
                "task_id": "task-001",
                "title": "Implement the change",
                "depends_on": [],
                "prompt": complete_prompt(),
                "expected_outputs": ["src/implemented.py"],
                "validation": [
                    {
                        "argv": ["python", "-m", "pytest", "tests"],
                        "working_directory": ".",
                        "acceptable_exit_codes": [0],
                        "timeout_seconds": 60,
                    }
                ],
            }
        ],
    }


def load_schema(name: str) -> dict:
    return json.loads((SCHEMA_ROOT / name).read_text(encoding="utf-8"))


def test_runtime_models_accept_exact_ready_and_blocked_shapes() -> None:
    request = BuildRequest(
        build_id="build-001",
        instruction="Complete build instruction.",
    )
    ready = BuildPlan.model_validate(ready_payload())
    blocked = BuildPlan.model_validate(
        {
            "build_id": "build-002",
            "status": "blocked",
            "gaps": ["The target repository path is missing."],
            "tasks": [],
        }
    )

    assert request.model_dump() == {
        "build_id": "build-001",
        "instruction": "Complete build instruction.",
    }
    assert ready.status == "ready"
    assert blocked.status == "blocked"


@pytest.mark.parametrize(
    "build_id",
    [
        "../escape",
        "..",
        "/absolute",
        r"C:\absolute",
        "build/child",
        r"build\child",
        "CON",
        "con.txt",
        "x" * 65,
    ],
)
def test_unsafe_build_ids_are_rejected(build_id: str) -> None:
    with pytest.raises(ValidationError):
        BuildRequest(build_id=build_id, instruction="work")


@pytest.mark.parametrize(
    "payload",
    [
        {**ready_payload(), "gaps": ["Still unresolved."]},
        {**ready_payload(), "tasks": []},
        {
            "build_id": "build-001",
            "status": "blocked",
            "gaps": [],
            "tasks": [],
        },
        {
            "build_id": "build-001",
            "status": "blocked",
            "gaps": ["Missing information."],
            "tasks": ready_payload()["tasks"],
        },
        {**ready_payload(), "unexpected": True},
    ],
)
def test_plan_status_invariants_and_extra_fields_are_rejected(payload: dict) -> None:
    with pytest.raises(ValidationError):
        BuildPlan.model_validate(payload)


def test_task_models_reject_unsafe_ids_multiline_titles_and_extras() -> None:
    with pytest.raises(ValidationError):
        BuildTask(
            task_id="../task",
            title="Unsafe",
            depends_on=[],
            prompt=complete_prompt(),
            expected_outputs=["src/implemented.py"],
            validation=[{"argv": ["python"], "working_directory": ".", "acceptable_exit_codes": [0], "timeout_seconds": 60}],
        )
    with pytest.raises(ValidationError):
        BuildTask(
            task_id="task-001",
            title="Two\nlines",
            depends_on=[],
            prompt=complete_prompt(),
            expected_outputs=["src/implemented.py"],
            validation=[{"argv": ["python"], "working_directory": ".", "acceptable_exit_codes": [0], "timeout_seconds": 60}],
        )
    payload = ready_payload()["tasks"][0] | {"extra": "not allowed"}
    with pytest.raises(ValidationError):
        BuildTask.model_validate(payload)
    with pytest.raises(ValidationError):
        BuildTask(
            task_id="CON",
            title="Reserved task",
            depends_on=[],
            prompt=complete_prompt(),
            expected_outputs=["src/implemented.py"],
            validation=[{"argv": ["python"], "working_directory": ".", "acceptable_exit_codes": [0], "timeout_seconds": 60}],
        )
    with pytest.raises(ValidationError):
        BuildTask(
            task_id="task-001",
            title="x" * 201,
            depends_on=[],
            prompt=complete_prompt(),
            expected_outputs=["src/implemented.py"],
            validation=[{"argv": ["python"], "working_directory": ".", "acceptable_exit_codes": [0], "timeout_seconds": 60}],
        )


def test_published_builder_schemas_match_runtime_examples() -> None:
    build_input = load_schema("build-input.schema.json")
    build_plan = load_schema("build-plan.schema.json")
    task_skill = load_schema("task-skill.schema.json")
    for schema in (build_input, build_plan, task_skill):
        Draft202012Validator.check_schema(schema)

    request = {
        "build_id": "build-001",
        "instruction": "Complete build instruction.",
    }
    plan = BuildPlan.model_validate(ready_payload()).model_dump(mode="json")
    skill = TaskSkill(
        task_id="task-001",
        prompt="# Complete generated SKILL.md\n",
    ).model_dump(mode="json")
    Draft202012Validator(build_input).validate(request)
    Draft202012Validator(build_plan).validate(plan)
    Draft202012Validator(task_skill).validate(skill)

    with pytest.raises(JsonSchemaError):
        Draft202012Validator(build_input).validate(
            {"build_id": "../escape", "instruction": "work"}
        )
