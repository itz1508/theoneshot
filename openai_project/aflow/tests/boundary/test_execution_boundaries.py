from __future__ import annotations

import ast
import importlib
import socket
import subprocess
from pathlib import Path

import pytest

import aflow
from aflow.analysis.decision_engine import analyze
from aflow.schemas.registry import schemas
from aflow.storage.atomic_write import OutputBoundaryError, atomic_write_json


def test_analysis_never_uses_subprocess_or_network(monkeypatch, clean_request):
    schemas()  # Preload local schema resources before installing runtime traps.

    def forbidden(*args, **kwargs):
        raise AssertionError("execution or network boundary crossed")

    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(subprocess, "check_output", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    assert analyze(clean_request)["decision"] == "no_material_gap"


def test_analysis_never_writes_or_mutates_protected_file(monkeypatch, clean_request, tmp_path):
    schemas()
    protected = tmp_path / "AGENTS.md"
    protected.write_text("authority", encoding="utf-8")
    before = protected.read_bytes()
    original_open = Path.open

    def guarded_open(self, mode="r", *args, **kwargs):
        if any(flag in mode for flag in "wax+"):
            raise AssertionError(f"write attempted: {self}")
        return original_open(self, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)
    assert analyze(clean_request)["execution_ready"] is True
    assert protected.read_bytes() == before


def test_atomic_writer_rejects_escape_and_analyzed_repository(tmp_path):
    output = tmp_path / "outputs"
    analyzed = tmp_path / "repository"
    output.mkdir()
    analyzed.mkdir()
    with pytest.raises(OutputBoundaryError):
        atomic_write_json(tmp_path / "escape.json", {}, output_root=output)
    with pytest.raises(OutputBoundaryError):
        atomic_write_json(analyzed / "result.json", {}, output_root=tmp_path, analyzed_repository=analyzed)
    with pytest.raises(OutputBoundaryError):
        atomic_write_json(output / ".codex" / "result.json", {}, output_root=output)
    with pytest.raises(OutputBoundaryError):
        atomic_write_json(output / "AGENTS.md", {}, output_root=output)
    atomic_write_json(output / "result.json", {"unicode": "café 東京"}, output_root=output, analyzed_repository=analyzed)
    assert (output / "result.json").read_text(encoding="utf-8").endswith("\n")


def test_import_boundary_contains_no_audisor_edge_docker_or_http_clients():
    source_root = Path(aflow.__file__).parent
    forbidden_roots = {"audisor", "edge", "docker", "requests", "httpx", "urllib", "subprocess"}
    violations = []
    for path in source_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                if name.split(".")[0].lower() in forbidden_roots:
                    violations.append((path.name, name))
    assert violations == []
