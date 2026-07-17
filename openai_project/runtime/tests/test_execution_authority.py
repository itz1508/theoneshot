"""Explicit target-root authority and baseline evidence."""

from pathlib import Path

import pytest
from dulwich.repo import Repo

from audisor.builder.authority import TargetAuthorityError, TargetAuthorityResolver
from audisor.schemas.execution import BuildExecutionRequest


def request(target: Path, allowed: list[str] | None = None) -> BuildExecutionRequest:
    return BuildExecutionRequest(
        execution_id="execution-001",
        idempotency_key="execution-request-001",
        target_root=str(target),
        allowed_write_paths=allowed or ["src", "tests"],
    )


def fixture_target(tmp_path: Path) -> Path:
    target = tmp_path / "target-project"
    (target / "src").mkdir(parents=True)
    (target / "tests").mkdir()
    (target / "README.md").write_text("fixture\n", encoding="utf-8")
    Repo.init(target).close()
    return target


def resolver(tmp_path: Path, *, approved: tuple[Path, ...] | None = None) -> TargetAuthorityResolver:
    return TargetAuthorityResolver(
        data_dir=tmp_path / "data",
        product_root=tmp_path / "product-source",
        reference_roots=(tmp_path / "reference",),
        approved_target_roots=approved if approved is not None else (tmp_path,),
    )


def resolve(resolver: TargetAuthorityResolver, target: Path):
    return resolver.resolve(
        "build-001",
        request(target),
        plan_hash="1" * 64,
        integrity_root="2" * 64,
        selected_provider="fake",
        workspace_path=resolver.data_dir / "builds/build-001/executions/execution-001/workspace",
    )


def test_authority_persists_requested_resolved_git_allowed_and_baseline(tmp_path: Path) -> None:
    target = fixture_target(tmp_path)
    authority = resolve(resolver(tmp_path), target)
    assert authority.record.requested_target_root == str(target)
    assert authority.record.resolved_target_root == str(target.resolve())
    assert authority.record.resolved_git_root == str(target.resolve())
    assert authority.record.allowed_write_paths == ["src", "tests"]
    assert len(authority.record.allowed_resolved_paths) == 2
    assert authority.baseline.tree_digest == authority.record.baseline_tree_digest
    assert all(".git" not in item.path for item in authority.baseline.inventory)
    assert authority.record.baseline_git_status


def test_authority_rejects_nonexistent_and_file_targets(tmp_path: Path) -> None:
    authority = resolver(tmp_path)
    for target in (tmp_path / "missing", tmp_path / "file.txt"):
        if target.suffix:
            target.write_text("x", encoding="utf-8")
        with pytest.raises(TargetAuthorityError):
            resolve(authority, target)


@pytest.mark.parametrize("protected_name", ["product-source", "data", "reference"])
def test_authority_rejects_product_data_and_reference_targets(
    tmp_path: Path, protected_name: str
) -> None:
    protected = tmp_path / protected_name
    (protected / "src").mkdir(parents=True)
    (protected / "tests").mkdir()
    authority = resolver(tmp_path)
    with pytest.raises(TargetAuthorityError, match="protected"):
        resolve(authority, protected)


def test_authority_rejects_target_outside_approved_roots(tmp_path: Path) -> None:
    target = fixture_target(tmp_path)
    authority = resolver(tmp_path, approved=(tmp_path / "different-authority",))
    with pytest.raises(TargetAuthorityError, match="approved"):
        resolve(authority, target)


def test_authority_rejects_missing_allowed_directory(tmp_path: Path) -> None:
    target = fixture_target(tmp_path)
    authority = resolver(tmp_path)
    invalid = request(target, ["missing"])
    with pytest.raises(TargetAuthorityError, match="Allowed"):
        authority.resolve(
            "build-001",
            invalid,
            plan_hash="1" * 64,
            integrity_root="2" * 64,
            selected_provider="fake",
            workspace_path=tmp_path / "workspace",
        )


def test_authority_baseline_detects_real_target_change(tmp_path: Path) -> None:
    target = fixture_target(tmp_path)
    authority_resolver = resolver(tmp_path)
    resolved = resolve(authority_resolver, target)
    assert authority_resolver.target_matches_baseline(target, resolved.baseline)
    (target / "src" / "changed.py").write_text("changed\n", encoding="utf-8")
    assert not authority_resolver.target_matches_baseline(target, resolved.baseline)


def test_authority_rejects_symlink_or_junction_target_content_when_supported(
    tmp_path: Path,
) -> None:
    target = fixture_target(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    link = target / "src" / "escape"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is unavailable")
    with pytest.raises(TargetAuthorityError, match="symlink|reparse"):
        resolve(resolver(tmp_path), target)
