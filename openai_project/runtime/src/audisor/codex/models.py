from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


@dataclass(frozen=True)
class CodexRunResult:
    operation_id: str
    build_id: str
    response: Any
    handoff_path: Path
    stdin_path: Path
    handoff_sha256: str
    stdin_sha256: str
    stdin_size_bytes: int
    resolved_command: tuple[str, ...]
    working_directory: Path
    pid: int | None
    exit_code: int
    outcome: str


@dataclass(frozen=True)
class PreparedBuildContext:
    build_id: str
    build_path: Path
    target_root: Path
    allowed_write_paths: tuple[str, ...]
    context: Mapping[str, Any]
