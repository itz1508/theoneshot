"""Cryptographic consistency checks for immutable prepared-build artifacts."""

import json
from pathlib import Path

import pytest

from audisor.builder.skill_renderer import render_skills
from audisor.builder.store import BuildStore
from audisor.builder.task_loader import (
    PreparedBuildBlockedError,
    PreparedBuildIntegrityError,
    PreparedBuildLoader,
)
from audisor.schemas.build import BuildPlan, BuildRequest


def complete_prompt(label: str) -> str:
    return f"""## Objective
Complete {label}.

## Inputs and repository paths
Use the prepared target paths.

## Required work
Implement {label}.

## Ordered steps
1. Inspect the workspace.
2. Implement and validate {label}.

## Expected output
Return the completed {label}.

## Validation
Run focused validation.

## Evidence to return
Return changed paths and command evidence."""


def ready_plan(build_id: str = "build-001") -> BuildPlan:
    return BuildPlan.model_validate(
        {
            "build_id": build_id,
            "status": "ready",
            "gaps": [],
            "tasks": [
                {
                    "task_id": "task-001",
                    "title": "Create module",
                    "depends_on": [],
                    "prompt": complete_prompt("module"),
                    "expected_outputs": ["src/module.py"],
                    "validation": [{"argv": ["python", "-m", "pytest", "tests"], "working_directory": ".", "acceptable_exit_codes": [0], "timeout_seconds": 60}],
                },
                {
                    "task_id": "task-002",
                    "title": "Create tests",
                    "depends_on": ["task-001"],
                    "prompt": complete_prompt("tests"),
                    "expected_outputs": ["tests/test_module.py"],
                    "validation": [{"argv": ["python", "-m", "pytest", "tests"], "working_directory": ".", "acceptable_exit_codes": [0], "timeout_seconds": 60}],
                },
            ],
        }
    )


def publish_ready(data_dir: Path, build_id: str = "build-001") -> tuple[BuildStore, Path]:
    store = BuildStore(data_dir)
    request = BuildRequest(build_id=build_id, instruction="Create module and tests.")
    plan = ready_plan(build_id)
    return store, store.publish(request, plan, render_skills(build_id, plan.tasks))


def test_loader_verifies_all_hashes_and_canonical_skills(tmp_path: Path) -> None:
    store, path = publish_ready(tmp_path / "data")
    loaded = PreparedBuildLoader(store).load("build-001")
    assert loaded.plan.status == "ready"
    assert list(loaded.skills) == ["task-001", "task-002"]
    assert len(loaded.plan_hash) == 64
    assert len(loaded.integrity_root) == 64
    assert (path / "integrity.json").is_file()


def test_integrity_manifest_is_deterministic_for_equivalent_preparations(
    tmp_path: Path,
) -> None:
    _first_store, first = publish_ready(tmp_path / "one")
    _second_store, second = publish_ready(tmp_path / "two")
    assert (first / "integrity.json").read_bytes() == (
        second / "integrity.json"
    ).read_bytes()


@pytest.mark.parametrize("filename", ["instruction.json", "plan.json"])
def test_loader_rejects_altered_instruction_or_plan_before_execution(
    tmp_path: Path, filename: str
) -> None:
    store, path = publish_ready(tmp_path / "data")
    artifact = path / filename
    artifact.write_bytes(artifact.read_bytes() + b" ")
    with pytest.raises(PreparedBuildIntegrityError, match="hash"):
        PreparedBuildLoader(store).load("build-001")


def test_loader_rejects_altered_or_missing_skill(tmp_path: Path) -> None:
    store, path = publish_ready(tmp_path / "data")
    skill = next((path / "skills").glob("*/SKILL.md"))
    skill.write_text(skill.read_text(encoding="utf-8") + "altered\n", encoding="utf-8")
    with pytest.raises(PreparedBuildIntegrityError, match="hash"):
        PreparedBuildLoader(store).load("build-001")

    store2, path2 = publish_ready(tmp_path / "other", "build-002")
    next((path2 / "skills").glob("*/SKILL.md")).unlink()
    with pytest.raises(PreparedBuildIntegrityError, match="inventory"):
        PreparedBuildLoader(store2).load("build-002")


def test_loader_rejects_extra_or_renamed_skill(tmp_path: Path) -> None:
    store, path = publish_ready(tmp_path / "data")
    extra = path / "skills" / "extra-task" / "SKILL.md"
    extra.parent.mkdir()
    extra.write_text("extra", encoding="utf-8")
    with pytest.raises(PreparedBuildIntegrityError, match="inventory"):
        PreparedBuildLoader(store).load("build-001")


def test_loader_rejects_legacy_build_without_integrity_manifest(tmp_path: Path) -> None:
    store, path = publish_ready(tmp_path / "data")
    (path / "integrity.json").unlink()
    with pytest.raises(PreparedBuildIntegrityError, match="unavailable"):
        PreparedBuildLoader(store).load("build-001")


def test_loader_rejects_manifest_root_or_duplicate_path_mismatch(tmp_path: Path) -> None:
    store, path = publish_ready(tmp_path / "data")
    manifest_path = path / "integrity.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["root_digest"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(PreparedBuildIntegrityError, match="root"):
        PreparedBuildLoader(store).load("build-001")


def test_blocked_prepared_plan_is_rejected_before_worker_execution(tmp_path: Path) -> None:
    store = BuildStore(tmp_path / "data")
    request = BuildRequest(build_id="blocked-001", instruction="Prepare work.")
    plan = BuildPlan.model_validate(
        {
            "build_id": "blocked-001",
            "status": "blocked",
            "gaps": ["The target path is missing."],
            "tasks": [],
        }
    )
    store.publish(request, plan, [])
    with pytest.raises(PreparedBuildBlockedError):
        PreparedBuildLoader(store).load("blocked-001")


def test_loader_rejects_skill_symlink_substitution_when_supported(tmp_path: Path) -> None:
    store, path = publish_ready(tmp_path / "data")
    skill = next((path / "skills").glob("*/SKILL.md"))
    original = tmp_path / "outside-skill.md"
    original.write_bytes(skill.read_bytes())
    skill.unlink()
    try:
        skill.symlink_to(original)
    except OSError:
        pytest.skip("symlink creation is unavailable")
    with pytest.raises(PreparedBuildIntegrityError, match="reparse"):
        PreparedBuildLoader(store).load("build-001")


def test_loader_rejects_build_root_symlink_substitution_when_supported(
    tmp_path: Path,
) -> None:
    store, path = publish_ready(tmp_path / "data")
    outside = tmp_path / "outside-build"
    path.rename(outside)
    try:
        path.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is unavailable")
    with pytest.raises(PreparedBuildIntegrityError, match="root is a reparse"):
        PreparedBuildLoader(store).load("build-001")
