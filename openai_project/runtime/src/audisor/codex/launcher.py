from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Callable


class CodexLaunchError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _command(path: str) -> list[str]:
    if path.casefold().endswith(".ps1"):
        powershell = shutil.which("powershell.exe") or shutil.which("pwsh.exe")
        if not powershell:
            raise CodexLaunchError("codex_process_start_failed", "PowerShell is unavailable")
        return [powershell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", path]
    return [path]


def resolve_codex(*, runner: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> tuple[str, tuple[str, ...], str]:
    path = shutil.which("codex")
    if not path:
        raise CodexLaunchError("codex_not_found", "Codex executable was not found on PATH")
    base = _command(path)
    try:
        version = runner([*base, "--version"], text=True, capture_output=True, check=False)
        help_result = runner([*base, "exec", "--help"], text=True, capture_output=True, check=False)
    except OSError as exc:
        raise CodexLaunchError("codex_version_check_failed", "Codex version check failed") from exc
    if version.returncode != 0 or "codex" not in (version.stdout or "").casefold():
        raise CodexLaunchError("codex_version_check_failed", "Installed command is not a supported Codex CLI")
    help_text = f"{help_result.stdout}\n{help_result.stderr}".casefold()
    if help_result.returncode != 0 or "codex exec" not in help_text or "stdin" not in help_text:
        raise CodexLaunchError("codex_launch_contract_unsupported", "codex exec stdin mode is unavailable")
    return path, tuple(base), (version.stdout or version.stderr or "").strip()


def launch_codex(*, stdin_bytes: bytes, cwd: Path, runner: Callable[..., subprocess.Popen] = subprocess.Popen) -> tuple[int | None, int, str, tuple[str, ...]]:
    path, base, _version = resolve_codex()
    argv = [*base, "exec", "-"]
    process = None
    try:
        process = runner(argv, cwd=str(cwd), stdin=subprocess.PIPE)
        pid = getattr(process, "pid", None)
        if process.stdin is None:
            raise CodexLaunchError("codex_process_start_failed", "Codex stdin was unavailable")
        process.stdin.write(stdin_bytes)
        process.stdin.close()
        exit_code = process.wait()
        return pid, exit_code, "codex_completed" if exit_code == 0 else "codex_failed", tuple(argv)
    except KeyboardInterrupt:
        if process is not None:
            try:
                process.terminate()
                process.wait(timeout=10)
            except Exception:
                pass
        return getattr(process, "pid", None), 130, "codex_cancelled", tuple(argv)
    except CodexLaunchError:
        raise
    except OSError as exc:
        raise CodexLaunchError("codex_process_start_failed", "Codex process could not be started") from exc
