from __future__ import annotations

from types import SimpleNamespace

from audisor.codex import launcher


def test_resolves_codex_and_validates_exec_stdin(monkeypatch):
    monkeypatch.setattr(launcher.shutil, "which", lambda name: "C:/tools/codex.exe" if name == "codex" else name)

    def run(argv, **kwargs):
        if argv[-1] == "--version":
            return SimpleNamespace(returncode=0, stdout="codex-cli 0.144.5", stderr="")
        return SimpleNamespace(returncode=0, stdout="Usage: codex exec\nstdin", stderr="")

    path, command, version = launcher.resolve_codex(runner=run)
    assert path == "C:/tools/codex.exe"
    assert command == ("C:/tools/codex.exe",)
    assert version == "codex-cli 0.144.5"


def test_launch_uses_argument_vector_and_exact_stdin(monkeypatch, tmp_path):
    monkeypatch.setattr(launcher, "resolve_codex", lambda: ("C:/tools/codex.exe", ("C:/tools/codex.exe",), "codex-cli 0.144.5"))
    captured = {}

    class Process:
        pid = 42

        def __init__(self):
            self.stdin = self

        def write(self, value):
            captured["stdin"] = value

        def close(self):
            captured["closed"] = True

        def wait(self):
            return 7

        def communicate(self):
            return (b"fake stdout", b"fake stderr")

        returncode = 7

    def runner(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return Process()

    pid, exit_code, outcome, argv, stdout_text, stderr_text = launcher.launch_codex(stdin_bytes=b"sealed", cwd=tmp_path, runner=runner)
    assert pid == 42
    assert exit_code == 7
    assert outcome == "codex_failed"
    assert argv == ("C:/tools/codex.exe", "exec", "-")
    assert stdout_text == "fake stdout"
    assert stderr_text == "fake stderr"
    assert captured["stdin"] == b"sealed"
    assert captured["kwargs"]["cwd"] == str(tmp_path)


def test_powershell_shim_uses_fixed_argument_vector(monkeypatch):
    monkeypatch.setattr(launcher.shutil, "which", lambda name: "C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe" if name == "powershell.exe" else None)
    assert launcher._command("C:/tools/codex.ps1") == [
        "C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        "C:/tools/codex.ps1",
    ]
