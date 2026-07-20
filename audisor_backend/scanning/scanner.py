"""Deterministic, read-only repository scanner for A-Flow Fix."""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from audisor_backend.schemas.fix.models import Finding, FindingsList


DEFAULT_EXCLUDED = {".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache", "snapshot"}
SECRET_RE = re.compile(r"(?i)(api[_-]?key|secret|token|password)\s*[:=]\s*['\"][^'\"]+['\"]")
AUTHORITY_WORDS = ("controller", "runner", "executor", "apply", "release", "ignite", "mutation")


@dataclass(frozen=True)
class ScanConfig:
    excluded_dirs: frozenset[str] = frozenset(DEFAULT_EXCLUDED)
    excluded_files: frozenset[str] = frozenset({"scanning/scanner.py"})
    extensions: frozenset[str] = frozenset({".py", ".json", ".toml", ".yaml", ".yml", ".ini", ".cfg", ".txt", ".js", ".ts"})
    test_commands: tuple[tuple[str, ...], ...] = ()
    # The runner is injected so the scanner never chooses or executes a mutating command.
    test_runner: Callable[[tuple[str, ...], Path], tuple[int, str, str]] | None = None
    contract_requirements: tuple[tuple[str, tuple[str, ...]], ...] = ()
    source_roots: tuple[str, ...] = ()
    repository_root: str | None = None


@dataclass(frozen=True)
class ScanReport:
    findings: FindingsList
    affected_items: tuple[str, ...]


def _canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _finding(kind: str, file: str, severity: str, evidence: dict) -> Finding:
    payload = {"type": kind, "file": file, "severity": severity, "evidence": evidence}
    identity = hashlib.sha256(_canonical(payload).encode()).hexdigest()[:16]
    return Finding(f"scan-{identity}", kind, file, severity, evidence)


def _relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


class DeterministicScanner:
    """The scanner only reads files. Commands are opt-in and supplied by callers."""

    def __init__(self, config: ScanConfig | None = None):
        self.config = config or ScanConfig()

    def _files(self, root: Path) -> list[Path]:
        return sorted(
            path for path in root.rglob("*")
            if path.is_file() and path.suffix.lower() in self.config.extensions
            and not any(part in self.config.excluded_dirs for part in path.relative_to(root).parts)
            and path.relative_to(root).as_posix() not in self.config.excluded_files
        )

    def _resolution_roots(self, scan_root: Path) -> list[Path]:
        roots = [Path(self.config.repository_root).resolve()] if self.config.repository_root else [scan_root.resolve()]
        roots.extend((roots[0] / value).resolve() for value in self.config.source_roots)
        # A package subtree is commonly scanned directly; its parent is still a valid source root.
        roots.extend([scan_root.resolve(), scan_root.parent.resolve()])
        return list(dict.fromkeys(path for path in roots if path.exists()))

    def scan(self, root: str | Path) -> ScanReport:
        base = Path(root).resolve()
        findings: list[Finding] = []
        files = self._files(base)
        contents: dict[str, str] = {}
        for path in files:
            rel = _relative(base, path)
            try:
                contents[rel] = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError) as exc:
                findings.append(_finding("correctness.broken_reference", rel, "medium", {"reason": type(exc).__name__, "repro": f"read {rel} as UTF-8"}))
                continue
            findings.extend(self._syntax(rel, contents[rel]))
            findings.extend(self._security(rel, contents[rel]))
            findings.extend(self._configuration(rel, contents[rel]))
        findings.extend(self._duplicates(contents))
        findings.extend(self._overlaps(contents))
        findings.extend(self._authority_paths(contents))
        findings.extend(self._dependencies(base, contents, self._resolution_roots(base), files))
        findings.extend(self._contracts(contents))
        findings.extend(self._tests(base))
        findings = sorted(findings, key=lambda f: (f.file, f.type, f.id))
        affected = tuple(sorted({f.file for f in findings}))
        return ScanReport(findings, affected)

    def _syntax(self, rel: str, text: str) -> list[Finding]:
        if rel.endswith(".py"):
            try:
                ast.parse(text, filename=rel)
            except SyntaxError as exc:
                return [_finding("correctness.syntax_error", rel, "high", {"line": exc.lineno, "column": exc.offset, "message": exc.msg, "repro": f"python -m py_compile {rel}"})]
        if rel.endswith(".json"):
            try:
                json.loads(text)
            except json.JSONDecodeError as exc:
                return [_finding("configuration.invalid_configuration", rel, "high", {"line": exc.lineno, "column": exc.colno, "message": exc.msg, "repro": f"python -m json.tool {rel}"})]
        return []

    def _security(self, rel: str, text: str) -> list[Finding]:
        result = []
        match = SECRET_RE.search(text)
        if match:
            result.append(_finding("security.hardcoded_secret", rel, "critical", {"line": text[:match.start()].count("\n") + 1, "rule": "credential-like literal", "repro": f"search credential patterns in {rel}"}))
        if rel.endswith(".py"):
            try:
                tree = ast.parse(text, filename=rel)
            except SyntaxError:
                tree = None
            if tree and any(isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in {"eval", "exec"} for node in ast.walk(tree)):
                result.append(_finding("security.unsafe_command", rel, "high", {"rule": "dynamic code execution", "repro": f"inspect eval/exec calls in {rel}"}))
        return result

    def _configuration(self, rel: str, text: str) -> list[Finding]:
        result = []
        if rel.lower() in {".env", "config.ini"}:
            return result
        if rel.endswith((".toml", ".yaml", ".yml", ".ini", ".cfg")) and not text.strip():
            result.append(_finding("configuration.invalid_configuration", rel, "medium", {"rule": "empty required configuration file", "repro": f"test non-empty configuration {rel}"}))
        if rel.endswith(".py") and "TODO(" in text:
            result.append(_finding("correctness.missing_required_implementation", rel, "medium", {"rule": "unimplemented TODO marker", "repro": f"search TODO( in {rel}"}))
        return result

    def _duplicates(self, contents: dict[str, str]) -> list[Finding]:
        by_hash: dict[str, list[str]] = {}
        for rel, text in contents.items():
            normalized = "\n".join(line.strip() for line in text.splitlines() if line.strip())
            if len(normalized) >= 20:
                by_hash.setdefault(hashlib.sha256(normalized.encode()).hexdigest(), []).append(rel)
        result = []
        for digest, paths in sorted(by_hash.items()):
            if len(paths) > 1:
                for rel in sorted(paths):
                    result.append(_finding("structure.duplicate_implementation", rel, "high", {"duplicate_files": sorted(paths), "content_sha256": digest, "repro": "compare normalized file hashes"}))
        return result

    def _overlaps(self, contents: dict[str, str]) -> list[Finding]:
        symbols: dict[str, list[str]] = {}
        for rel, text in contents.items():
            if not rel.endswith(".py"):
                continue
            try:
                tree = ast.parse(text, filename=rel)
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and not node.name.startswith("_"):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and self._is_interface_member(node, tree):
                        continue
                    body = ast.dump(node, annotate_fields=True, include_attributes=False)
                    symbols.setdefault(f"{node.name}:{hashlib.sha256(body.encode()).hexdigest()}", []).append(rel)
        return [
            _finding("structure.duplicate_implementation", paths[0], "medium", {"symbol": name.split(":", 1)[0], "files": sorted(set(paths)), "repro": f"compare concrete definitions of {name.split(':', 1)[0]}"})
            for name, paths in sorted(symbols.items()) if len(set(paths)) > 1
        ]

    @staticmethod
    def _is_interface_member(node: ast.AST, tree: ast.AST) -> bool:
        for parent in ast.walk(tree):
            if not isinstance(parent, ast.ClassDef) or node not in parent.body:
                continue
            bases = {base.id for base in parent.bases if isinstance(base, ast.Name)} | {base.attr for base in parent.bases if isinstance(base, ast.Attribute)}
            decorators = {d.id for d in getattr(node, "decorator_list", []) if isinstance(d, ast.Name)} | {d.attr for d in getattr(node, "decorator_list", []) if isinstance(d, ast.Attribute)}
            body_is_stub = len(getattr(node, "body", [])) == 1 and isinstance(node.body[0], (ast.Pass, ast.Expr))
            return bool({"Protocol", "ABC"} & bases or "abstractmethod" in decorators or body_is_stub)
        return False

    def _authority_paths(self, contents: dict[str, str]) -> list[Finding]:
        candidates = [rel for rel in contents if Path(rel).stem.lower() in AUTHORITY_WORDS]
        if len(candidates) < 2:
            return []
        return [_finding("authority.competing_authority_path", rel, "high", {"authority_candidates": sorted(candidates), "repro": "inspect active authority-named modules"}) for rel in sorted(candidates)]

    def _dependencies(self, root: Path, contents: dict[str, str], resolution_roots: list[Path], files: list[Path]) -> list[Finding]:
        result = []
        declared: dict[str, list[dict[str, str]]] = {}
        for rel, text in contents.items():
            if Path(rel).name.lower() in {"requirements.txt", "pyproject.toml", "package.json", "package-lock.json"}:
                for name, constraint in self._dependency_declarations(rel, text):
                    declared.setdefault(name.lower(), []).append({"file": rel, "constraint": constraint})
        for name, declarations in sorted(declared.items()):
            files_for_dependency = sorted({item["file"] for item in declarations})
            if len(files_for_dependency) <= 1:
                continue
            constraints = {item["constraint"] for item in declarations}
            evidence = {
                "dependency": name,
                "declarations": declarations,
                "repro": "compare dependency constraints across declaration files",
            }
            if len(constraints) > 1:
                result.append(_finding("dependency.conflicting_constraints", files_for_dependency[0], "high", evidence))
            else:
                result.append(_finding("dependency.duplicate_declaration", files_for_dependency[0], "medium", evidence))
        for rel, text in contents.items():
            if not rel.endswith(".py"):
                continue
            try:
                tree = ast.parse(text, filename=rel)
            except SyntaxError:
                continue
            source_file = root / rel
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imports = [(alias.name, 0) for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        imports = [(f"{'.' * node.level}{node.module}", node.level)]
                    else:
                        imports = [(f"{'.' * node.level}{alias.name}", node.level) for alias in node.names]
                else:
                    continue
                for imported, level in imports:
                    state = self._resolve_import(imported, level, source_file, resolution_roots)
                    if state in {"missing", "ambiguous", "resolver_error"}:
                        top = imported.lstrip(".").split(".", 1)[0].lower()
                        result.append(_finding("dependency.unresolved", rel, "high", {"module": imported, "resolver_state": state, "line": getattr(node, "lineno", None), "affected_symbol": top, "repro": f"resolve Python import {imported} from {rel}"}))
        return result

    @staticmethod
    def _dependency_declarations(rel: str, text: str) -> list[tuple[str, str]]:
        """Extract declared constraints without pretending to resolve versions."""
        result: list[tuple[str, str]] = []
        filename = Path(rel).name.lower()
        if filename == "pyproject.toml":
            for name, value in re.findall(r"(?im)^\s*[\"']?([A-Za-z][A-Za-z0-9_.-]*)[\"']?\s*=\s*[\"']([^\"']+)[\"']", text):
                result.append((name.lower(), value.strip()))
        elif filename == "requirements.txt":
            pattern = re.compile(
                r"(?im)^\s*([A-Za-z][A-Za-z0-9_.-]*)\s*(?:(===|==|!=|~=|>=|<=|>|<)\s*([^\s#;,'\"]+))?"
            )
            for match in pattern.finditer(text):
                name, operator, value = match.groups()
                result.append((name.lower(), f"{operator or 'unspecified'}{value or ''}"))
        else:
            for name, value in re.findall(r"(?i)[\"']([A-Za-z][A-Za-z0-9_.-]*)[\"']\s*:\s*[\"']([^\"']+)[\"']", text):
                result.append((name.lower(), value.strip()))
        return result

    @staticmethod
    def _resolve_import(imported: str, level: int, source_file: Path, roots: list[Path]) -> str:
        try:
            if level:
                package_dir = source_file.parent
                for _ in range(max(level - 1, 0)):
                    package_dir = package_dir.parent
                module = imported.lstrip(".")
                candidate = package_dir / Path(*module.split("."))
                if candidate.with_suffix(".py").is_file() or candidate.is_dir():
                    return "local_repository"
                return "missing"
            top = imported.split(".", 1)[0]
            local = []
            for root in roots:
                candidate = root / Path(*imported.split("."))
                if candidate.with_suffix(".py").is_file() or candidate.is_dir():
                    local.append(candidate)
                elif (root / top).is_dir():
                    local.append(root / top)
            if len({str(path.resolve()) for path in local}) > 1:
                return "ambiguous"
            if local:
                return "local_repository"
            if top in getattr(sys, "stdlib_module_names", set()) or top in sys.builtin_module_names:
                return "standard_library"
            try:
                return "installed_external" if importlib.util.find_spec(top) else "missing"
            except (ImportError, ModuleNotFoundError, ValueError):
                return "resolver_error"
        except OSError:
            return "resolver_error"

    def _contracts(self, contents: dict[str, str]) -> list[Finding]:
        result = []
        for rel, required in self.config.contract_requirements:
            if rel not in contents:
                result.append(_finding("schema.contract_violation", rel, "high", {"missing_fields": list(required), "reason": "contract artifact missing", "repro": f"inspect required contract {rel}"}))
                continue
            try:
                value = json.loads(contents[rel])
            except json.JSONDecodeError:
                continue
            missing = [field for field in required if field not in value]
            if missing:
                result.append(_finding("schema.contract_violation", rel, "high", {"missing_fields": missing, "required_fields": list(required), "repro": f"validate contract fields in {rel}"}))
        return result

    def _tests(self, root: Path) -> list[Finding]:
        if not self.config.test_runner:
            return []
        result = []
        for command in self.config.test_commands:
            code, stdout, stderr = self.config.test_runner(command, root)
            if code:
                result.append(_finding("test.regression_failure", command[0], "high", {"command": list(command), "exit_code": code, "stdout": stdout, "stderr": stderr, "repro": "rerun the recorded validation command"}))
        return result


def scan_repository(root: str | Path, config: ScanConfig | None = None) -> ScanReport:
    return DeterministicScanner(config).scan(root)
