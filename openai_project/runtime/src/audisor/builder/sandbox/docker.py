"""Docker-backed, fail-closed Phase 2B command isolation."""

from __future__ import annotations

import os
import queue
import subprocess
import threading
import time
from pathlib import Path
from typing import Mapping, Sequence

from audisor.builder.sandbox.base import SandboxResult, SandboxRunner, SandboxUnavailableError


class DockerSandboxRunner(SandboxRunner):
    """Execute a command in a disposable Docker container, never on the host."""

    # This tag is product-owned and built from infra/sandbox/Dockerfile.  It is
    # deliberately not a host interpreter fallback.
    DEFAULT_IMAGE = "audisor-sandbox:phase2b-v1"
    MAX_STREAM_BYTES = 65_536
    MAX_TOTAL_OUTPUT_BYTES = 131_072

    def __init__(
        self,
        *,
        image: str = DEFAULT_IMAGE,
        docker_executable: str = "docker",
        max_stream_bytes: int = MAX_STREAM_BYTES,
        max_total_output_bytes: int = MAX_TOTAL_OUTPUT_BYTES,
        memory_limit: str = "512m",
        cpu_limit: str = "1.0",
        pids_limit: int = 64,
    ) -> None:
        if not image or max_stream_bytes < 1 or max_total_output_bytes < max_stream_bytes:
            raise ValueError("Invalid Docker sandbox limits")
        self.image = image
        self.docker_executable = docker_executable
        self.max_stream_bytes = max_stream_bytes
        self.max_total_output_bytes = max_total_output_bytes
        self.memory_limit = memory_limit
        self.cpu_limit = cpu_limit
        self.pids_limit = pids_limit

    @staticmethod
    def _safe_workdir(value: str) -> str:
        if value in {"", "."}:
            return "/workspace"
        normalized = value.replace("\\", "/")
        if normalized.startswith("/") or "/../" in f"/{normalized}/" or normalized == "..":
            raise ValueError("sandbox working directory must be workspace-relative")
        return f"/workspace/{normalized.strip('/')}"

    @staticmethod
    def _controlled_environment(environment: Mapping[str, str] | None) -> list[str]:
        # Do not inherit host values.  These are the entire forwarded environment.
        values = {
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
            "PYTHONUTF8": "1",
            "PYTEST_ADDOPTS": "-p no:cacheprovider",
            "NO_COLOR": "1",
        }
        if environment:
            for name, value in environment.items():
                if name in values:
                    values[name] = value
        return [item for name, value in sorted(values.items()) for item in ("--env", f"{name}={value}")]

    def build_command(
        self,
        argv: Sequence[str],
        *,
        workspace: Path,
        working_directory: str,
        environment: Mapping[str, str] | None = None,
    ) -> list[str]:
        """Return the exact fail-closed ``docker run`` argv for one command."""
        if not argv or any(not isinstance(value, str) or not value for value in argv):
            raise ValueError("sandbox argv must contain non-empty strings")
        root = workspace.resolve(strict=True)
        if not root.is_dir() or root.is_symlink():
            raise ValueError("sandbox workspace must be a real directory")
        mount = f"type=bind,src={root},dst=/workspace,rw"
        return [
            self.docker_executable,
            "run",
            "--rm",
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            str(self.pids_limit),
            "--memory",
            self.memory_limit,
            "--cpus",
            self.cpu_limit,
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=64m",
            "--mount",
            mount,
            "--workdir",
            self._safe_workdir(working_directory),
            "--user",
            "65534:65534",
            *self._controlled_environment(environment),
            self.image,
            *argv,
        ]

    @staticmethod
    def _reader(stream, name: str, events: queue.Queue[tuple[str, bytes | None]]) -> None:
        try:
            while chunk := stream.read(8192):
                events.put((name, chunk))
        finally:
            events.put((name, None))

    def run(
        self,
        argv: Sequence[str],
        *,
        workspace: Path,
        working_directory: str,
        timeout_seconds: int,
        environment: Mapping[str, str] | None = None,
    ) -> SandboxResult:
        if timeout_seconds < 1:
            raise ValueError("sandbox timeout must be positive")
        command = self.build_command(
            argv, workspace=workspace, working_directory=working_directory, environment=environment
        )
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                start_new_session=os.name != "nt",
            )
        except OSError as exc:
            raise SandboxUnavailableError("Docker sandbox is unavailable") from exc

        assert process.stdout is not None and process.stderr is not None
        events: queue.Queue[tuple[str, bytes | None]] = queue.Queue()
        readers = [
            threading.Thread(target=self._reader, args=(process.stdout, "stdout", events), daemon=True),
            threading.Thread(target=self._reader, args=(process.stderr, "stderr", events), daemon=True),
        ]
        for reader in readers:
            reader.start()
        output = {"stdout": bytearray(), "stderr": bytearray()}
        closed: set[str] = set()
        limited = False
        timed_out = False
        started = time.monotonic()
        while len(closed) < 2:
            remaining = timeout_seconds - (time.monotonic() - started)
            if remaining <= 0:
                timed_out = True
                process.kill()
                break
            try:
                stream, chunk = events.get(timeout=min(remaining, 0.1))
            except queue.Empty:
                if process.poll() is not None:
                    continue
                continue
            if chunk is None:
                closed.add(stream)
                continue
            total = len(output["stdout"]) + len(output["stderr"])
            available = min(self.max_stream_bytes - len(output[stream]), self.max_total_output_bytes - total)
            if available > 0:
                output[stream].extend(chunk[:available])
            if len(chunk) > available:
                limited = True
                process.kill()
                break
        if timed_out or limited:
            process.wait(timeout=5)
        else:
            process.wait(timeout=max(1, timeout_seconds))
        for reader in readers:
            reader.join(timeout=1)
        # Drain only already-queued bounded chunks after process termination.
        while not events.empty():
            stream, chunk = events.get_nowait()
            if chunk is None:
                continue
            total = len(output["stdout"]) + len(output["stderr"])
            available = min(self.max_stream_bytes - len(output[stream]), self.max_total_output_bytes - total)
            if available > 0:
                output[stream].extend(chunk[:available])
            if len(chunk) > available:
                limited = True
        return SandboxResult(
            argv=tuple(argv),
            exit_code=None if timed_out or limited else process.returncode,
            stdout=bytes(output["stdout"]).decode("utf-8", errors="backslashreplace"),
            stderr=bytes(output["stderr"]).decode("utf-8", errors="backslashreplace"),
            timed_out=timed_out,
            output_limit_exceeded=limited,
        )
