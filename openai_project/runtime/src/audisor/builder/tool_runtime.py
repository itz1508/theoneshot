"""Workspace-confined mutation execution for secure Phase 2B."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Callable

from audisor.builder.authority import is_reparse_or_symlink, is_within
from audisor.builder.evidence import sanitize_text, utc_now
from audisor.schemas.execution import (
    ActionExecutionRecord,
    ChangeRecord,
    CommandEvidence,
    WorkerActionPlan,
)

CONTROL_NAMES = {
    ".audisor",
    ".git",
    "authority.json",
    "baseline.json",
    "evidence",
    "idempotency",
    "request.json",
    "results",
    "state.json",
    "terminal-manifest.json",
    "workspace.json",
}


class ToolRuntimeError(RuntimeError):
    """A workspace mutation failed after available evidence was captured."""

    def __init__(
        self,
        message: str,
        *,
        actions: list[ActionExecutionRecord] | None = None,
        commands: list[CommandEvidence] | None = None,
        changes: list[ChangeRecord] | None = None,
    ) -> None:
        super().__init__(message)
        self.actions = actions or []
        self.commands = commands or []
        self.changes = changes or []


def tree_snapshot(root: Path) -> dict[str, tuple[str, str | None]]:
    records: dict[str, tuple[str, str | None]] = {}
    for current, directories, files in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        for name in list(directories):
            path = current_path / name
            relative = path.relative_to(root).as_posix()
            if is_reparse_or_symlink(path):
                records[relative] = ("reparse", None)
                directories.remove(name)
            else:
                records[relative] = ("directory", None)
        for name in files:
            path = current_path / name
            relative = path.relative_to(root).as_posix()
            if is_reparse_or_symlink(path):
                records[relative] = ("reparse", None)
                continue
            try:
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
            except OSError:
                digest = None
            records[relative] = ("file", digest)
    return records


def changes_between(
    before: dict[str, tuple[str, str | None]],
    after: dict[str, tuple[str, str | None]],
) -> list[ChangeRecord]:
    changes: list[ChangeRecord] = []
    for path in sorted(set(before) | set(after), key=lambda value: (value.casefold(), value)):
        old = before.get(path)
        new = after.get(path)
        if old == new:
            continue
        change = "created" if old is None else "deleted" if new is None else "modified"
        changes.append(
            ChangeRecord(
                path=path,
                change=change,
                sha256_before=old[1] if old and old[0] == "file" else None,
                sha256_after=new[1] if new and new[0] == "file" else None,
            )
        )
    return changes


class ToolRuntime:
    """Apply a closed mutation plan inside approved resolved workspace roots."""

    def __init__(
        self,
        workspace_root: Path,
        allowed_relative_paths: tuple[str, ...],
        command_temp: Path | None = None,
        **_ignored: object,
    ) -> None:
        self.workspace_root = workspace_root.resolve(strict=True)
        self.allowed_paths = tuple(
            (self.workspace_root / Path(*path.replace("\\", "/").split("/"))).resolve(
                strict=False
            )
            for path in allowed_relative_paths
        )
        if not self.allowed_paths or any(
            not is_within(path, self.workspace_root) for path in self.allowed_paths
        ):
            raise ToolRuntimeError("Allowed workspace roots are invalid")

    def resolved_allowed_paths(self) -> tuple[str, ...]:
        return tuple(str(path) for path in self.allowed_paths)

    def verify_expected_outputs(self, paths: list[str]) -> None:
        for relative in paths:
            resolved = self._resolve(relative, must_exist=True)
            if not resolved.exists():
                raise ToolRuntimeError("An expected output is missing")

    def _resolve(self, relative: str, *, must_exist: bool = False) -> Path:
        parts = relative.replace("\\", "/").split("/")
        if any(part.casefold() in CONTROL_NAMES for part in parts):
            raise ToolRuntimeError("A mutation targeted execution metadata")
        candidate = self.workspace_root / Path(*parts)
        current = self.workspace_root
        for part in parts:
            current = current / part
            if current.exists() and is_reparse_or_symlink(current):
                raise ToolRuntimeError("A mutation crossed a symlink or reparse point")
        resolved = candidate.resolve(strict=False)
        if not is_within(resolved, self.workspace_root):
            raise ToolRuntimeError("A mutation escaped the isolated workspace")
        if not any(is_within(resolved, root) for root in self.allowed_paths):
            raise ToolRuntimeError("A mutation exceeded allowed workspace roots")
        if must_exist and not resolved.exists():
            raise ToolRuntimeError("A mutation target does not exist")
        return resolved

    def execute(
        self,
        plan: WorkerActionPlan,
        progress: Callable[
            [list[ActionExecutionRecord], list[CommandEvidence]], None
        ],
    ) -> tuple[list[ActionExecutionRecord], list[CommandEvidence], list[ChangeRecord]]:
        baseline = tree_snapshot(self.workspace_root)
        actions: list[ActionExecutionRecord] = []
        for mutation in plan.mutations:
            record = ActionExecutionRecord(
                action_id=mutation.action_id,
                type=mutation.type,
                status="running",
                start_timestamp=utc_now(),
                path=mutation.path,
            )
            actions.append(record)
            progress(actions, [])
            try:
                path = self._resolve(
                    mutation.path,
                    must_exist=mutation.type == "delete_file",
                )
                byte_count: int | None = None
                content_hash: str | None = None
                if mutation.type == "write_file":
                    if not path.parent.is_dir():
                        raise ToolRuntimeError("write_file parent directory does not exist")
                    content = mutation.content.encode("utf-8", errors="strict")
                    path.write_bytes(content)
                    byte_count = len(content)
                    content_hash = hashlib.sha256(content).hexdigest()
                elif mutation.type == "create_directory":
                    path.mkdir(parents=False, exist_ok=False)
                elif mutation.type == "delete_file":
                    if not path.is_file():
                        raise ToolRuntimeError("delete_file target is not a file")
                    path.unlink()
                else:  # pragma: no cover - discriminated schema is closed
                    raise ToolRuntimeError("Unknown mutation type")
                actions[-1] = record.model_copy(
                    update={
                        "status": "completed",
                        "end_timestamp": utc_now(),
                        "byte_count": byte_count,
                        "content_sha256": content_hash,
                    }
                )
                progress(actions, [])
            except (OSError, UnicodeError, ToolRuntimeError) as exc:
                message, _ = sanitize_text(exc, limit=1000)
                actions[-1] = record.model_copy(
                    update={
                        "status": "failed",
                        "end_timestamp": utc_now(),
                        "message": message,
                    }
                )
                progress(actions, [])
                raise ToolRuntimeError(
                    message,
                    actions=actions,
                    changes=changes_between(baseline, tree_snapshot(self.workspace_root)),
                ) from None

        final_changes = changes_between(baseline, tree_snapshot(self.workspace_root))
        expected = {
            path.replace("\\", "/").casefold()
            for path in plan.expected_changed_paths
        }
        actual = {change.path.casefold() for change in final_changes}
        if expected != actual:
            raise ToolRuntimeError(
                "Actual changed paths do not match expected_changed_paths",
                actions=actions,
                changes=final_changes,
            )
        return actions, [], final_changes


MutationRuntime = ToolRuntime
