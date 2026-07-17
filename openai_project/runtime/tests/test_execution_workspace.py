"""Verified isolated workspace construction and reconciliation."""

from pathlib import Path

import pytest
from dulwich.repo import Repo

from audisor.builder.authority import TargetAuthorityResolver
from audisor.builder.workspace import WorkspaceError, WorkspaceManager
from audisor.schemas.execution import BuildExecutionRequest


def resolved_fixture(tmp_path: Path):
    target = tmp_path / "target"
    (target / "src").mkdir(parents=True)
    (target / "tests").mkdir()
    (target / "src" / "existing.py").write_text("VALUE = 1\n", encoding="utf-8")
    Repo.init(target).close()
    resolver = TargetAuthorityResolver(
        data_dir=tmp_path / "data",
        product_root=tmp_path / "product",
        reference_roots=(tmp_path / "reference",),
        approved_target_roots=(tmp_path,),
    )
    request = BuildExecutionRequest(
        execution_id="execution-001",
        idempotency_key="key-001",
        target_root=str(target),
        allowed_write_paths=["src", "tests"],
    )
    return resolver.resolve(
        "build-001",
        request,
        plan_hash="1" * 64,
        integrity_root="2" * 64,
        selected_provider="fake",
        workspace_path=tmp_path / "data/workspace",
    )


def test_workspace_is_verified_copy_without_git_metadata(tmp_path: Path) -> None:
    authority = resolved_fixture(tmp_path)
    workspace = tmp_path / "execution" / "workspace"
    record = WorkspaceManager().create(
        authority.resolved_target, workspace, authority.baseline
    )
    assert record.baseline_verified is True
    assert record.workspace_tree_digest == authority.baseline.tree_digest
    assert (workspace / "src" / "existing.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    assert not (workspace / ".git").exists()


def test_workspace_creation_does_not_change_target(tmp_path: Path) -> None:
    authority = resolved_fixture(tmp_path)
    before = authority.baseline.tree_digest
    WorkspaceManager().create(
        authority.resolved_target, tmp_path / "execution/workspace", authority.baseline
    )
    resolver = TargetAuthorityResolver(
        data_dir=tmp_path / "other-data",
        product_root=tmp_path / "other-product",
        reference_roots=(tmp_path / "other-reference",),
    )
    assert resolver.target_matches_baseline(authority.resolved_target, authority.baseline)
    assert authority.baseline.tree_digest == before


def test_workspace_rejects_existing_destination(tmp_path: Path) -> None:
    authority = resolved_fixture(tmp_path)
    destination = tmp_path / "execution/workspace"
    destination.mkdir(parents=True)
    with pytest.raises(WorkspaceError, match="already exists"):
        WorkspaceManager().create(
            authority.resolved_target, destination, authority.baseline
        )


def test_workspace_rejects_incomplete_or_mismatched_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    authority = resolved_fixture(tmp_path)
    original = WorkspaceManager.create

    class BrokenManager(WorkspaceManager):
        def create(self, source, destination, baseline):
            destination.mkdir(parents=True)
            (destination / "wrong.txt").write_text("wrong", encoding="utf-8")
            return original(self, source, destination, baseline)

    with pytest.raises(WorkspaceError):
        BrokenManager().create(
            authority.resolved_target, tmp_path / "execution/workspace", authority.baseline
        )
