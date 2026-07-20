"""Bounded, evidence-backed dependency closure for Fix snapshots."""

from __future__ import annotations

import ast
from pathlib import Path


def _module_candidates(source: Path, module: str, level: int = 0) -> list[Path]:
    base = source.parent
    for _ in range(max(level - 1, 0)):
        base = base.parent
    parts = [part for part in module.split(".") if part]
    if not parts:
        return []
    candidate = base.joinpath(*parts)
    return [candidate.with_suffix(".py"), candidate / "__init__.py"]


def resolve_dependency_details(root: str | Path, findings) -> tuple[list[str], dict[str, list[dict[str, str]]]]:
    """Resolve only direct local Python imports from affected finding files."""
    base = Path(root).resolve()
    issue_files = {finding.file.replace("\\", "/"): finding for finding in findings}
    closure = set(issue_files)
    evidence: dict[str, list[dict[str, str]]] = {}
    for relative, finding in sorted(issue_files.items()):
        source = (base / relative).resolve()
        if not source.is_file() or base not in source.parents:
            raise FileNotFoundError(relative)
        try:
            tree = ast.parse(source.read_text(encoding="utf-8"), filename=relative)
        except (OSError, UnicodeDecodeError, SyntaxError):
            continue
        imports: list[tuple[str, int, int]] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend((alias.name, 0, getattr(node, "lineno", 0)) for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                line = getattr(node, "lineno", 0)
                imports.append((module, node.level, line))
                imports.extend((f"{module}.{alias.name}" if module else alias.name, node.level, line) for alias in node.names)
        for module, level, line in imports:
            target = next((candidate for candidate in _module_candidates(source, module, level) if candidate.is_file() and base in candidate.resolve().parents), None)
            if target is None:
                continue
            target_relative = target.resolve().relative_to(base).as_posix()
            if target_relative in closure:
                continue
            closure.add(target_relative)
            evidence.setdefault(target_relative, []).append({
                "originating_finding_id": finding.id,
                "dependency_source": relative,
                "dependency_target": target_relative,
                "inclusion_reason": "direct local import required to understand or validate affected file",
                "resolution_evidence": f"python import {module!r} at {relative}:{line}",
            })
    return sorted(closure), evidence


def resolve_dependency_closure(root: str | Path, files: list[str], findings=None) -> list[str]:
    """Compatibility wrapper returning bounded closure paths only."""
    if findings is None:
        class _Finding:
            def __init__(self, file: str):
                self.file = file
                self.id = "unknown"
        findings = [_Finding(value) for value in files]
    closure, _evidence = resolve_dependency_details(root, findings)
    return closure
