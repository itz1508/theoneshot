from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

from aflow.storage.hashing import seal
from aflow.domain.models import validate_domain_invariants


Clock = Callable[[], datetime]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


def _files(root: Path, relative_paths: Iterable[str]) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for relative in relative_paths:
        candidate = (root / relative).resolve()
        try:
            candidate.relative_to(root.resolve())
        except ValueError as exc:
            raise ValueError(f"baseline path escapes repository: {relative}") from exc
        if candidate.is_file():
            result[candidate.relative_to(root).as_posix()] = candidate
        elif candidate.is_dir():
            for path in sorted(candidate.rglob("*")):
                if path.is_file() and ".git" not in path.parts:
                    result[path.relative_to(root).as_posix()] = path
    return result


def capture_baseline(
    repository_root: str | Path,
    *,
    baseline_id: str,
    relevant_paths: list[str],
    protected_paths: list[str],
    authority_hashes: list[dict[str, str]],
    repository_kind: str = "filesystem",
    git_head: str | None = None,
    git_state: str = "unavailable",
    dirty_state: str = "untracked",
    clock: Clock = _now,
) -> dict:
    root = Path(repository_root).resolve()
    relevant = _files(root, relevant_paths)
    protected = _files(root, protected_paths)
    entries = []
    for relative, path in sorted({**relevant, **protected}.items()):
        entries.append({
            "path": relative,
            "content_hash": _file_hash(path),
            "classification": "protected" if relative in protected else "relevant",
        })
    value = {
        "schema_version": "1.0.0",
        "baseline_id": baseline_id,
        "repository_root": str(root),
        "repository_kind": repository_kind,
        "git_head": git_head,
        "git_state": git_state,
        "dirty_state": dirty_state,
        "scope": {"mode": "scoped", "relevant_paths": relevant_paths, "protected_paths": protected_paths},
        "entries": entries,
        "authority_hashes": authority_hashes,
        "captured_at": clock().astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    return validate_domain_invariants(seal(value), "baseline")
