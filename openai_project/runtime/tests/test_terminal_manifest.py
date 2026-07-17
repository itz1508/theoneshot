"""Terminal manifest publication and fail-closed evidence reconciliation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from audisor.builder.terminal_manifest import (
    TaskArtifactPaths,
    TerminalManifestError,
    require_valid_terminal_manifest,
    verify_terminal_manifest,
    write_terminal_manifest,
)


def execution_fixture(tmp_path: Path) -> tuple[Path, dict[str, TaskArtifactPaths]]:
    root = tmp_path / "execution-001"
    (root / "results").mkdir(parents=True)
    (root / "evidence/task-001").mkdir(parents=True)
    (root / "results/task-001.json").write_text(
        '{"task_id":"task-001","status":"completed"}\n', encoding="utf-8"
    )
    (root / "evidence/task-001/validation.json").write_text(
        '{"status":"completed","exit_codes":[0]}\n', encoding="utf-8"
    )
    (root / "authority.json").write_text('{"authority":"verified"}\n', encoding="utf-8")
    (root / "baseline.json").write_text('{"baseline":"verified"}\n', encoding="utf-8")
    return root, {
        "task-001": TaskArtifactPaths(
            result_path="results/task-001.json",
            evidence_paths=("evidence/task-001",),
        )
    }


def write_fixture(root: Path, tasks: dict[str, TaskArtifactPaths]):
    return write_terminal_manifest(
        root,
        build_id="build-001",
        execution_id="execution-001",
        expected_task_ids=["task-001"],
        task_artifacts=tasks,
        required_artifacts=["authority.json", "baseline.json"],
    )


def test_manifest_hashes_required_and_task_artifacts_and_verifies_again(
    tmp_path: Path,
) -> None:
    root, tasks = execution_fixture(tmp_path)

    written = write_fixture(root, tasks)
    verification = verify_terminal_manifest(
        root,
        expected_sha256=written.sha256,
        expected_task_ids=["task-001"],
    )

    assert written.path == root / "terminal-manifest.json"
    assert verification.valid is True
    assert verification.errors == ()
    assert require_valid_terminal_manifest(
        root,
        expected_sha256=written.sha256,
        expected_task_ids=["task-001"],
    ) == written.record
    paths = {artifact.path for artifact in written.record.artifacts}
    assert paths == {
        "authority.json",
        "baseline.json",
        "results/task-001.json",
        "evidence/task-001/validation.json",
    }
    persisted = json.loads(written.path.read_text(encoding="utf-8"))
    assert persisted["expected_task_ids"] == ["task-001"]


def test_missing_terminal_evidence_cannot_return_a_trusted_manifest(
    tmp_path: Path,
) -> None:
    root, tasks = execution_fixture(tmp_path)
    written = write_fixture(root, tasks)
    (root / "results/task-001.json").unlink()

    verification = verify_terminal_manifest(
        root,
        expected_sha256=written.sha256,
        expected_task_ids=["task-001"],
    )

    assert verification.valid is False
    assert any("missing or unsafe" in error for error in verification.errors)
    with pytest.raises(TerminalManifestError, match="reconciliation failed"):
        require_valid_terminal_manifest(
            root,
            expected_sha256=written.sha256,
            expected_task_ids=["task-001"],
        )


def test_tampered_artifact_and_manifest_hash_are_detected(tmp_path: Path) -> None:
    root, tasks = execution_fixture(tmp_path)
    written = write_fixture(root, tasks)
    (root / "authority.json").write_text('{"authority":"tampered"}\n', encoding="utf-8")

    artifact_check = verify_terminal_manifest(
        root, expected_sha256=written.sha256, expected_task_ids=["task-001"]
    )
    manifest_check = verify_terminal_manifest(
        root, expected_sha256="f" * 64, expected_task_ids=["task-001"]
    )

    assert artifact_check.valid is False
    assert any("hash mismatch: authority.json" in error for error in artifact_check.errors)
    assert manifest_check.valid is False
    assert "terminal manifest hash mismatch" in manifest_check.errors


def test_expected_task_mismatch_is_not_trusted(tmp_path: Path) -> None:
    root, tasks = execution_fixture(tmp_path)
    written = write_fixture(root, tasks)

    verification = verify_terminal_manifest(
        root,
        expected_sha256=written.sha256,
        expected_task_ids=["task-002"],
    )

    assert verification.valid is False
    assert "terminal manifest expected task mismatch" in verification.errors


def test_write_requires_exact_result_and_nonempty_evidence_for_every_task(
    tmp_path: Path,
) -> None:
    root, tasks = execution_fixture(tmp_path)
    missing = {
        "task-001": TaskArtifactPaths(
            result_path="results/task-001.json",
            evidence_paths=("evidence/task-001/missing.json",),
        )
    }

    with pytest.raises(TerminalManifestError, match="missing"):
        write_fixture(root, missing)
    with pytest.raises(TerminalManifestError, match="Every expected task"):
        write_terminal_manifest(
            root,
            build_id="build-001",
            execution_id="execution-001",
            expected_task_ids=["task-001"],
            task_artifacts={},
            required_artifacts=["authority.json"],
        )
    assert tasks["task-001"].result_path == "results/task-001.json"


def test_manifest_rejects_traversal_and_reparse_evidence(tmp_path: Path) -> None:
    root, tasks = execution_fixture(tmp_path)
    with pytest.raises(TerminalManifestError, match="unsafe"):
        write_terminal_manifest(
            root,
            build_id="build-001",
            execution_id="execution-001",
            expected_task_ids=["task-001"],
            task_artifacts=tasks,
            required_artifacts=["../outside.json"],
        )

    outside = tmp_path / "outside.json"
    outside.write_text("outside\n", encoding="utf-8")
    link = root / "linked.json"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is unavailable")
    with pytest.raises(TerminalManifestError, match="symlink|reparse"):
        write_terminal_manifest(
            root,
            build_id="build-001",
            execution_id="execution-001",
            expected_task_ids=["task-001"],
            task_artifacts=tasks,
            required_artifacts=["linked.json"],
        )
