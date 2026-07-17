"""Structural coverage and deterministic one-time SKILL.md rendering."""

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from audisor.builder.coverage import CoverageValidationError, validate_task_coverage
from audisor.builder.skill_renderer import (
    SkillRenderingError,
    render_skill,
    render_skills,
)
from audisor.schemas.build import BuildTask

SCHEMA_ROOT = Path(__file__).resolve().parents[2] / "schemas"


def complete_prompt() -> str:
    return """## Objective
Create the requested Python implementation.

## Inputs and repository paths
Use greeting.py and the repository test directory.

## Required work
Implement greet(name) and its tests.

## Ordered steps
1. Inspect the current package layout.
2. Create the implementation and tests.

## Expected output
Return greeting.py and its tests.

## Validation
Run the focused greeting tests.

## Evidence to return
Return changed paths and the test command output."""


def build_task(
    task_id: str = "task-001",
    title: str = "Create greeting module",
    prompt: str | None = None,
) -> BuildTask:
    return BuildTask(
        task_id=task_id,
        title=title,
        depends_on=[],
        prompt=prompt or complete_prompt(),
        expected_outputs=["greeting.py", "tests/test_greeting.py"],
        validation=[{"argv": ["python", "-m", "pytest", "tests"], "working_directory": ".", "acceptable_exit_codes": [0], "timeout_seconds": 60}],
    )


def test_renderer_creates_fixed_skill_and_exact_amd_mapping() -> None:
    rendered = render_skill("build-001", build_task())
    mapping = rendered.as_worker_task().model_dump()

    assert rendered.directory_name == "task-001-create-greeting-module"
    assert rendered.content.startswith(
        "---\n"
        "name: task-001-create-greeting-module\n"
        "description: One-time generated task for Audisor build build-001.\n"
        "---\n"
    )
    assert rendered.content.endswith("\n")
    assert "\r" not in rendered.content
    assert '"expected_outputs"' not in rendered.content
    assert '"argv": [' in rendered.content
    assert mapping == {
        "task_id": "task-001",
        "prompt": rendered.content,
    }
    schema = json.loads(
        (SCHEMA_ROOT / "task-skill.schema.json").read_text(encoding="utf-8")
    )
    Draft202012Validator(schema).validate(mapping)


@pytest.mark.parametrize(
    "mutator",
    [
        lambda prompt: prompt.replace("## Validation", "## Missing validation"),
        lambda prompt: prompt + "\n## Objective\nDuplicate.",
        lambda prompt: prompt.replace(
            "## Objective\nCreate the requested Python implementation.\n\n"
            "## Inputs and repository paths",
            "## Inputs and repository paths",
        )
        + "\n## Objective\nLate objective.",
        lambda prompt: prompt.replace(
            "## Expected output\nReturn greeting.py and its tests.",
            "## Expected output\nTBD",
        ),
        lambda prompt: prompt.replace(
            "## Required work\nImplement greet(name) and its tests.",
            "## Required work\n",
        ),
        lambda prompt: prompt.replace(
            "1. Inspect the current package layout.\n"
            "2. Create the implementation and tests.",
            "Inspect and implement.",
        ),
    ],
)
def test_missing_duplicate_reordered_empty_placeholder_and_unordered_sections_rejected(
    mutator,
) -> None:
    with pytest.raises(CoverageValidationError):
        validate_task_coverage(build_task(prompt=mutator(complete_prompt())))


def test_inputs_section_may_explicitly_require_no_external_path() -> None:
    prompt = complete_prompt().replace(
        "Use greeting.py and the repository test directory.",
        "No external repository path is required.",
    )
    validate_task_coverage(build_task(prompt=prompt))


def test_slugging_is_bounded_deterministic_and_has_empty_fallback() -> None:
    rendered = render_skill(
        "build-001",
        build_task(title="😀😀"),
    )
    assert rendered.directory_name == "task-001-task"
    assert len(rendered.directory_name) <= 120


def test_renderer_rejects_filesystem_equivalent_slug_collisions() -> None:
    with pytest.raises(SkillRenderingError, match="collide"):
        render_skills(
            "build-001",
            [
                build_task(task_id="task.a", title="Same"),
                build_task(task_id="task-a", title="Same"),
            ],
        )


def test_generated_skill_paths_are_not_permanent_agent_skill_paths(tmp_path: Path) -> None:
    rendered = render_skill("build-001", build_task())
    generated_path = tmp_path / "builds" / "build-001" / "skills" / rendered.directory_name
    assert ".agents" not in generated_path.parts
    assert generated_path.name == rendered.directory_name
