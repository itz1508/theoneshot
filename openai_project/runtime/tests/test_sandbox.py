"""Unit coverage for fail-closed Docker sandbox configuration."""

from pathlib import Path
import io
import time

import pytest

from audisor.builder.sandbox import DockerSandboxRunner, SandboxUnavailableError


def test_docker_command_has_required_containment_flags(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runner = DockerSandboxRunner(image="audisor-sandbox:phase2b-v1")
    command = runner.build_command(
        ["python", "-m", "pytest", "tests/test_one.py", "-q"],
        workspace=workspace,
        working_directory=".",
    )
    assert command[:3] == ["docker", "run", "--rm"]
    for flag in ("--network", "--read-only", "--cap-drop", "--security-opt", "--pids-limit", "--memory", "--cpus", "--tmpfs", "--mount", "--workdir", "--user"):
        assert flag in command
    assert command[command.index("--network") + 1] == "none"
    assert command[command.index("--cap-drop") + 1] == "ALL"
    assert command[command.index("--security-opt") + 1] == "no-new-privileges"
    assert command[command.index("--workdir") + 1] == "/workspace"
    assert command[command.index("--user") + 1] == "65534:65534"
    mount = command[command.index("--mount") + 1]
    assert f"src={workspace.resolve()}" in mount
    assert "dst=/workspace,rw" in mount
    assert command[-6:] == ["audisor-sandbox:phase2b-v1", "python", "-m", "pytest", "tests/test_one.py", "-q"]


def test_docker_runner_fails_closed_when_docker_cannot_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    def unavailable(*_args, **_kwargs):
        raise FileNotFoundError("docker unavailable")

    monkeypatch.setattr("audisor.builder.sandbox.docker.subprocess.Popen", unavailable)
    runner = DockerSandboxRunner()
    with pytest.raises(SandboxUnavailableError, match="unavailable"):
        runner.run(["python", "-V"], workspace=workspace, working_directory=".", timeout_seconds=1)


def test_sandbox_rejects_workspace_escape(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runner = DockerSandboxRunner()
    with pytest.raises(ValueError, match="workspace-relative"):
        runner.build_command(["python", "-V"], workspace=workspace, working_directory="../outside")


class FakeProcess:
    def __init__(self, stdout: bytes, stderr: bytes = b"") -> None:
        self.stdout = io.BytesIO(stdout)
        self.stderr = io.BytesIO(stderr)
        self.returncode = 0
        self.killed = False

    def poll(self):
        return self.returncode if self.killed else None

    def kill(self):
        self.killed = True
        self.returncode = -9

    def wait(self, timeout=None):
        return self.returncode


def test_output_is_bounded_while_reading_and_limit_is_durable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    process = FakeProcess(b"x" * 100_000, b"y" * 100_000)
    monkeypatch.setattr(
        "audisor.builder.sandbox.docker.subprocess.Popen", lambda *_a, **_k: process
    )
    runner = DockerSandboxRunner(max_stream_bytes=1024, max_total_output_bytes=1536)
    result = runner.run(
        ["python", "spam.py"],
        workspace=workspace,
        working_directory=".",
        timeout_seconds=5,
    )
    assert result.output_limit_exceeded is True
    assert process.killed is True
    assert len(result.stdout.encode()) <= 1024
    assert len(result.stderr.encode()) <= 1024
    assert len(result.stdout.encode()) + len(result.stderr.encode()) <= 1536


class SlowStream:
    def __init__(self) -> None:
        self.done = False

    def read(self, _size: int) -> bytes:
        if self.done:
            return b""
        time.sleep(1.2)
        self.done = True
        return b""


class SlowProcess(FakeProcess):
    def __init__(self) -> None:
        super().__init__(b"")
        self.stdout = SlowStream()
        self.stderr = SlowStream()


def test_timeout_terminates_sandbox_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    process = SlowProcess()
    monkeypatch.setattr(
        "audisor.builder.sandbox.docker.subprocess.Popen", lambda *_a, **_k: process
    )
    result = DockerSandboxRunner().run(
        ["python", "slow.py"],
        workspace=workspace,
        working_directory=".",
        timeout_seconds=1,
    )
    assert result.timed_out is True
    assert process.killed is True
