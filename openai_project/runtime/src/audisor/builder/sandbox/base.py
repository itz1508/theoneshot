"""The narrow command-execution boundary used by Phase 2B."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence


class SandboxUnavailableError(RuntimeError):
    """The configured OS sandbox cannot be used; host fallback is forbidden."""


@dataclass(frozen=True)
class SandboxResult:
    """Bounded, sanitized-at-the-caller command result."""

    argv: tuple[str, ...]
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool
    output_limit_exceeded: bool


class SandboxRunner(ABC):
    """Run an argv only inside an isolated execution workspace."""

    @abstractmethod
    def run(
        self,
        argv: Sequence[str],
        *,
        workspace: Path,
        working_directory: str,
        timeout_seconds: int,
        environment: Mapping[str, str] | None = None,
    ) -> SandboxResult:
        """Run one command, or raise ``SandboxUnavailableError`` without fallback."""
