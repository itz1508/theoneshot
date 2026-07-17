from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


class OutputBoundaryError(ValueError):
    pass


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def atomic_write_json(
    path: str | Path,
    value: Any,
    *,
    output_root: str | Path,
    analyzed_repository: str | Path | None = None,
) -> None:
    target = Path(path).resolve()
    root = Path(output_root).resolve()
    if not _inside(target, root):
        raise OutputBoundaryError(f"output escapes approved root: {target}")
    lowered_parts = {part.lower() for part in target.parts}
    if lowered_parts & {".git", ".codex"} or target.name.lower() == "agents.md":
        raise OutputBoundaryError("A-Flow must not write protected authority or repository-control paths")
    if analyzed_repository is not None and _inside(target, Path(analyzed_repository)):
        raise OutputBoundaryError("A-Flow must not write into the analyzed repository")
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise
