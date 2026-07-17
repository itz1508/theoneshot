"""Atomic prepared-build storage and overwrite protection."""

import hashlib
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from audisor.builder.skill_renderer import render_skills
from audisor.builder.skill_renderer import RenderedSkill
from audisor.builder.store import (
    BuildAlreadyExistsError,
    BuildStore,
    BuildStoreError,
)
from audisor.schemas.build import BuildPlan, BuildRequest


def complete_prompt() -> str:
    return """## Objective
Create the implementation.

## Inputs and repository paths
Use the current repository root.

## Required work
Implement the requested files.

## Ordered steps
1. Inspect the active files.
2. Implement and validate the change.

## Expected output
Return the completed files.

## Validation
Run focused tests.

## Evidence to return
Return changed paths and test output."""


def ready_plan(build_id: str = "build-001") -> BuildPlan:
    return BuildPlan.model_validate(
        {
            "build_id": build_id,
            "status": "ready",
            "gaps": [],
            "tasks": [
                {
                    "task_id": "task-001",
                    "title": "Create implementation",
                    "depends_on": [],
                    "prompt": complete_prompt(),
                    "expected_outputs": ["src/implementation.py"],
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
    )


def prepared(build_id: str = "build-001"):
    request = BuildRequest(build_id=build_id, instruction="Complete the build.")
    plan = ready_plan(build_id)
    return request, plan, render_skills(build_id, plan.tasks)


def tree_hash(root: Path) -> str:
    records = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        records.append(
            f"{path.relative_to(root).as_posix()}\t"
            f"{hashlib.sha256(path.read_bytes()).hexdigest()}"
        )
    return hashlib.sha256("\n".join(records).encode()).hexdigest()


def assert_no_writer_residue(store: BuildStore) -> None:
    root = store.builds_root
    if root.exists():
        assert not list(root.glob(".*.tmp"))
        assert not list(root.glob(".*.lock"))


def test_store_uses_project_default_and_configured_external_data_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AUDISOR_DATA_DIR", raising=False)
    default_store = BuildStore.from_environment()
    if os.name == "nt":
        expected = Path(os.environ["LOCALAPPDATA"]) / "Audisor" / "data"
    else:
        expected = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "audisor"
    assert default_store.data_dir.resolve() == expected.resolve()

    configured = tmp_path / "configured-data"
    monkeypatch.setenv("AUDISOR_DATA_DIR", str(configured))
    assert BuildStore.from_environment().data_dir.resolve() == configured.resolve()


def test_store_publishes_instruction_plan_and_one_skill(tmp_path: Path) -> None:
    store = BuildStore(tmp_path / "external-data")
    request, plan, skills = prepared()
    final = store.publish(request, plan, skills)

    assert final == tmp_path / "external-data" / "builds" / "build-001"
    assert (final / "instruction.json").is_file()
    assert (final / "plan.json").is_file()
    skill_files = list((final / "skills").glob("*/SKILL.md"))
    assert len(skill_files) == 1
    assert skill_files[0].read_text(encoding="utf-8") == skills[0].content
    assert ".agents" not in skill_files[0].parts
    assert_no_writer_residue(store)


def test_blocked_plan_persists_without_skill_files(tmp_path: Path) -> None:
    store = BuildStore(tmp_path / "data")
    request = BuildRequest(build_id="blocked-001", instruction="Prepare the build.")
    plan = BuildPlan.model_validate(
        {
            "build_id": "blocked-001",
            "status": "blocked",
            "gaps": ["The target module name is missing."],
            "tasks": [],
        }
    )
    final = store.publish(request, plan, [])

    assert (final / "instruction.json").is_file()
    assert (final / "plan.json").is_file()
    assert list((final / "skills").iterdir()) == []


def test_store_rejects_traversal_at_writer_boundary(tmp_path: Path) -> None:
    store = BuildStore(tmp_path / "data")
    with pytest.raises(ValueError):
        store.build_path("../escape")
    assert not (tmp_path / "escape").exists()


def test_store_rejects_invalid_skill_mapping_before_creating_storage(
    tmp_path: Path,
) -> None:
    store = BuildStore(tmp_path / "data")
    request, plan, skills = prepared()
    invalid = [
        RenderedSkill(
            task_id="other-task",
            directory_name=skills[0].directory_name,
            content=skills[0].content,
        )
    ]
    with pytest.raises(BuildStoreError, match="storage failed"):
        store.publish(request, plan, invalid)
    assert not store.data_dir.exists()


def test_existing_build_is_not_overwritten(tmp_path: Path) -> None:
    store = BuildStore(tmp_path / "data")
    request, plan, skills = prepared()
    final = store.publish(request, plan, skills)
    before = tree_hash(final)

    with pytest.raises(BuildAlreadyExistsError):
        store.publish(request, plan, skills)

    assert tree_hash(final) == before


def test_injected_write_failure_leaves_no_partial_build(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = BuildStore(tmp_path / "data")
    request, plan, skills = prepared()
    original = BuildStore._write_text
    calls = 0

    def failing_write(self: BuildStore, path: Path, content: str) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected write failure")
        original(self, path, content)

    monkeypatch.setattr(BuildStore, "_write_text", failing_write)
    with pytest.raises(BuildStoreError, match="storage failed"):
        store.publish(request, plan, skills)

    assert not store.build_path("build-001").exists()
    assert_no_writer_residue(store)


def test_injected_rename_failure_leaves_no_partial_build(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = BuildStore(tmp_path / "data")
    request, plan, skills = prepared()

    def failing_publish(self: BuildStore, source: Path, destination: Path) -> None:
        raise OSError("injected rename failure")

    monkeypatch.setattr(BuildStore, "_publish_directory", failing_publish)
    with pytest.raises(BuildStoreError, match="storage failed"):
        store.publish(request, plan, skills)

    assert not store.build_path("build-001").exists()
    assert_no_writer_residue(store)


def test_atomic_publish_sees_complete_temp_before_final_rename(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = BuildStore(tmp_path / "data")
    request, plan, skills = prepared()
    original = BuildStore._publish_directory
    observed = False

    def inspecting_publish(self: BuildStore, source: Path, destination: Path) -> None:
        nonlocal observed
        assert not destination.exists()
        assert (source / "instruction.json").is_file()
        assert (source / "plan.json").is_file()
        assert len(list((source / "skills").glob("*/SKILL.md"))) == 1
        observed = True
        original(self, source, destination)

    monkeypatch.setattr(BuildStore, "_publish_directory", inspecting_publish)
    final = store.publish(request, plan, skills)
    assert observed
    assert final.is_dir()
    assert_no_writer_residue(store)


def test_two_concurrent_writers_produce_one_build_and_one_conflict(
    tmp_path: Path,
) -> None:
    store = BuildStore(tmp_path / "data")
    request, plan, skills = prepared()

    def publish() -> str:
        try:
            store.publish(request, plan, skills)
        except BuildAlreadyExistsError:
            return "conflict"
        return "published"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _index: publish(), range(2)))

    assert sorted(results) == ["conflict", "published"]
    final = store.build_path("build-001")
    assert (final / "instruction.json").is_file()
    assert (final / "plan.json").is_file()
    assert_no_writer_residue(store)


def test_foreign_lock_is_not_removed(tmp_path: Path) -> None:
    store = BuildStore(tmp_path / "data")
    request, plan, skills = prepared()
    store.builds_root.mkdir(parents=True)
    lock = store.builds_root / ".build-001.lock"
    lock.write_text("foreign", encoding="utf-8")

    with pytest.raises(BuildAlreadyExistsError):
        store.publish(request, plan, skills)

    assert lock.read_text(encoding="utf-8") == "foreign"
    assert not store.build_path("build-001").exists()
