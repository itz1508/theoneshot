"""Explicit target authority and immutable target-baseline capture."""

from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from dulwich import porcelain
from dulwich.errors import NotGitRepository
from dulwich.repo import Repo

from audisor.builder.evidence import canonical_json_bytes, sanitize_text, sha256_bytes, utc_now
from audisor.schemas.execution import (
    BaselineFileRecord,
    BuildExecutionRequest,
    GitInspectionEvidence,
    TargetAuthorityRecord,
    TargetBaseline,
)


class TargetAuthorityError(RuntimeError):
    """The requested target cannot be safely authorized."""


def normalized_path(path: Path) -> str:
    return os.path.normcase(os.path.normpath(str(path.resolve())))


def is_within(path: Path, root: Path) -> bool:
    try:
        return os.path.commonpath([normalized_path(path), normalized_path(root)]) == normalized_path(root)
    except ValueError:
        return False


def paths_overlap(first: Path, second: Path) -> bool:
    return is_within(first, second) or is_within(second, first)


def is_reparse_or_symlink(path: Path) -> bool:
    try:
        status = path.lstat()
    except OSError:
        return True
    attributes = getattr(status, "st_file_attributes", 0)
    flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return path.is_symlink() or bool(attributes & flag)


def _git_context(
    target: Path,
) -> tuple[str | None, list[str], list[GitInspectionEvidence]]:
    """Inspect repository state in-process; never launch a host Git command."""
    try:
        repository = Repo.discover(target)
    except NotGitRepository:
        return None, [], [
            GitInspectionEvidence(
                operation="discover",
                status="not_a_repository",
            )
        ]
    except Exception as exc:
        message, _ = sanitize_text(exc)
        return None, [], [
            GitInspectionEvidence(
                operation="discover",
                status="inspection_failed",
                detail=message,
            )
        ]

    git_root = Path(repository.path).resolve()
    discovery = GitInspectionEvidence(
        operation="discover",
        status="repository_found",
    )
    try:
        status = porcelain.status(
            repository,
            ignored=False,
            untracked_files="all",
        )
    except Exception as exc:
        message, _ = sanitize_text(exc)
        return str(git_root), [], [
            discovery,
            GitInspectionEvidence(
                operation="status",
                status="inspection_failed",
                detail=message,
            ),
        ]
    finally:
        repository.close()

    lines: list[str] = []
    for category in sorted(status.staged):
        lines.extend(
            f"staged:{category}:{os.fsdecode(path)}"
            for path in sorted(status.staged[category])
        )
    lines.extend(f"unstaged:{os.fsdecode(path)}" for path in sorted(status.unstaged))
    lines.extend(f"untracked:{os.fsdecode(path)}" for path in sorted(status.untracked))
    status_kind = "dirty" if lines else "clean"
    return str(git_root), lines, [
        discovery,
        GitInspectionEvidence(
            operation="status",
            status=status_kind,
            detail=f"entries={len(lines)}",
        ),
    ]


def capture_tree(root: Path) -> tuple[list[BaselineFileRecord], str]:
    """Capture copyable files/directories without following Git or reparses."""
    records: list[BaselineFileRecord] = []
    for current, directories, files in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        if current_path == root and ".git" in directories:
            directories.remove(".git")
        for name in list(directories):
            directory = current_path / name
            if is_reparse_or_symlink(directory):
                raise TargetAuthorityError("Target contains a symlink or reparse point")
            relative = directory.relative_to(root).as_posix()
            records.append(
                BaselineFileRecord(path=relative, kind="directory", size=0, sha256=None)
            )
        for name in files:
            file_path = current_path / name
            if is_reparse_or_symlink(file_path):
                raise TargetAuthorityError("Target contains a symlink or reparse point")
            relative = file_path.relative_to(root).as_posix()
            try:
                content = file_path.read_bytes()
            except OSError:
                raise TargetAuthorityError("Target baseline could not be read") from None
            records.append(
                BaselineFileRecord(
                    path=relative,
                    kind="file",
                    size=len(content),
                    sha256=hashlib.sha256(content).hexdigest(),
                )
            )
    records.sort(key=lambda item: (item.path.casefold(), item.path, item.kind))
    digest = sha256_bytes(
        canonical_json_bytes([record.model_dump(mode="json") for record in records])
    )
    return records, digest


@dataclass(frozen=True)
class ResolvedTargetAuthority:
    record: TargetAuthorityRecord
    baseline: TargetBaseline
    resolved_target: Path
    allowed_relative_paths: tuple[str, ...]


class TargetAuthorityResolver:
    """Resolve one request and reject overlap with product, data, snapshots, or refs."""

    def __init__(
        self,
        *,
        data_dir: Path,
        product_root: Path | None = None,
        reference_roots: tuple[Path, ...] | None = None,
        approved_target_roots: tuple[Path, ...] = (),
    ) -> None:
        self.data_dir = data_dir.resolve()
        self.product_root = (
            product_root.resolve()
            if product_root is not None
            else Path(__file__).resolve().parents[4]
        )
        self.snapshot_root = self.product_root.parent / "snapshot"
        development_root = self.product_root.parent.parent
        self.reference_roots = reference_roots or (
            self.product_root.parent / "audisor",
            development_root / "amd",
            development_root / ("hackaton" + "-uipath-jun29-workbench"),
            development_root / "Edge",
        )
        self.approved_target_roots = tuple(path.resolve() for path in approved_target_roots)

    def _reject_protected_overlap(self, target: Path) -> None:
        protected = (
            self.data_dir,
            self.product_root,
            self.snapshot_root,
            *(path.resolve() for path in self.reference_roots if path.exists()),
        )
        if any(paths_overlap(target, root) for root in protected):
            raise TargetAuthorityError("Target overlaps a protected repository or data root")
        if self.approved_target_roots and not any(
            is_within(target, root) for root in self.approved_target_roots
        ):
            raise TargetAuthorityError("Target is outside the approved authority roots")

    def resolve(
        self,
        build_id: str,
        request: BuildExecutionRequest,
        *,
        plan_hash: str,
        integrity_root: str,
        selected_provider: str,
        workspace_path: Path,
    ) -> ResolvedTargetAuthority:
        requested = request.target_root
        target = Path(requested).expanduser()
        if not target.exists():
            raise TargetAuthorityError("Target root does not exist")
        target = target.resolve()
        if not target.is_dir() or is_reparse_or_symlink(target):
            raise TargetAuthorityError("Target root must resolve to a real directory")
        self._reject_protected_overlap(target)

        allowed_resolved: list[str] = []
        for supplied in request.allowed_write_paths:
            relative_parts = supplied.replace("\\", "/").split("/")
            candidate = (target / Path(*relative_parts)).resolve()
            if not is_within(candidate, target):
                raise TargetAuthorityError("Allowed path escapes the target root")
            if not candidate.is_dir() or is_reparse_or_symlink(candidate):
                raise TargetAuthorityError("Allowed paths must resolve to real directories")
            allowed_resolved.append(str(candidate))

        inventory, digest = capture_tree(target)
        git_root, git_status, git_evidence = _git_context(target)
        captured = utc_now()
        baseline = TargetBaseline(
            captured_at=captured,
            inventory=inventory,
            tree_digest=digest,
            git_status=git_status,
            resolved_git_root=git_root,
            git_evidence=git_evidence,
        )
        request_payload = {
            "build_id": build_id,
            **request.model_dump(mode="json"),
            "prepared_integrity_root": integrity_root,
        }
        request_digest = sha256_bytes(canonical_json_bytes(request_payload))
        record = TargetAuthorityRecord(
            build_id=build_id,
            execution_id=request.execution_id,
            idempotency_key=request.idempotency_key,
            request_digest=request_digest,
            requested_target_root=requested,
            resolved_target_root=str(target),
            resolved_git_root=git_root,
            allowed_write_paths=request.allowed_write_paths,
            allowed_resolved_paths=allowed_resolved,
            baseline_file_inventory=inventory,
            baseline_tree_digest=digest,
            baseline_git_status=git_status,
            baseline_git_evidence=git_evidence,
            prepared_plan_hash=plan_hash,
            prepared_integrity_root=integrity_root,
            selected_provider=selected_provider,
            authority_timestamp=captured,
            isolated_workspace_path=str(workspace_path.resolve()),
        )
        return ResolvedTargetAuthority(
            record=record,
            baseline=baseline,
            resolved_target=target,
            allowed_relative_paths=tuple(request.allowed_write_paths),
        )

    @staticmethod
    def target_matches_baseline(
        target: Path,
        baseline: TargetBaseline,
    ) -> bool:
        try:
            inventory, digest = capture_tree(target)
        except TargetAuthorityError:
            return False
        git_root, git_status, git_evidence = _git_context(target)
        return (
            digest == baseline.tree_digest
            and inventory == baseline.inventory
            and git_root == baseline.resolved_git_root
            and git_status == baseline.git_status
            and git_evidence == baseline.git_evidence
        )
