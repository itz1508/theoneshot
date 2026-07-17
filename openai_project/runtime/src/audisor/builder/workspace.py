"""Verified target-baseline copy into a per-execution workspace."""

from __future__ import annotations

import shutil
from pathlib import Path

from audisor.builder.authority import TargetAuthorityError, capture_tree
from audisor.builder.evidence import utc_now
from audisor.schemas.execution import TargetBaseline, WorkspaceRecord


class WorkspaceError(RuntimeError):
    """The isolated workspace could not be created or reconciled."""


class WorkspaceManager:
    """Create a copy that excludes Git internals and matches the target baseline."""

    def create(
        self,
        source: Path,
        destination: Path,
        baseline: TargetBaseline,
    ) -> WorkspaceRecord:
        if destination.exists():
            raise WorkspaceError("Execution workspace already exists")

        def ignore(directory: str, entries: list[str]) -> set[str]:
            if Path(directory).resolve() == source.resolve() and ".git" in entries:
                return {".git"}
            return set()

        try:
            shutil.copytree(
                source,
                destination,
                symlinks=True,
                ignore=ignore,
                copy_function=shutil.copy2,
            )
            inventory, digest = capture_tree(destination)
        except (OSError, shutil.Error, TargetAuthorityError):
            if destination.exists():
                shutil.rmtree(destination, ignore_errors=True)
            raise WorkspaceError("Execution workspace creation failed") from None

        if inventory != baseline.inventory or digest != baseline.tree_digest:
            shutil.rmtree(destination, ignore_errors=True)
            raise WorkspaceError("Execution workspace baseline does not match")
        return WorkspaceRecord(
            created_at=utc_now(),
            source_root=str(source.resolve()),
            workspace_root=str(destination.resolve()),
            baseline_tree_digest=baseline.tree_digest,
            workspace_tree_digest=digest,
            baseline_verified=True,
            excluded_paths=[".git"],
        )

