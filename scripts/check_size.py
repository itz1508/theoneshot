"""Deterministic repository size-policy checker.

Enforces docs/code-size-policy.md using physical_lines and AST
symbol_span. Standard library only. Output is sorted and free of
timestamps and absolute paths; every path is repository-relative POSIX.

Exit codes: 0 = pass (warnings allowed), 1 = policy failure,
2 = malformed configuration.
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from datetime import date
from pathlib import Path

SCAN_ROOTS = ("openai_project", "audisor_backend", "local-server", "packaging", "scripts")
EXCLUDED_PARTS = frozenset(
    {".venv", "venv", "node_modules", "__pycache__", ".git", ".pytest_cache",
     "build", "dist", ".eggs"}
)
EXEMPT_CATEGORIES = frozenset(
    {"generated code", "vendored code", "migrations", "declarative schemas",
     "static data", "snapshots", "generated protocol bindings", "lock files"}
)

PROD_MODULE_REVIEW, PROD_MODULE_HARD = 400, 500
TEST_MODULE_REVIEW = 500
FUNCTION_REVIEW, FUNCTION_HARD = 60, 100
CLASS_REVIEW = 200


class ConfigError(Exception):
    """Malformed baseline or exception configuration."""


def is_test_path(rel_posix: str) -> bool:
    parts = rel_posix.split("/")
    name = parts[-1]
    return "tests" in parts or name.startswith("test_") or name == "conftest.py"


def iter_python_files(repo_root: Path, scan_roots: tuple[str, ...]) -> list[Path]:
    files: list[Path] = []
    for root in scan_roots:
        base = repo_root / root
        if not base.exists():
            continue
        for path in base.rglob("*.py"):
            if not EXCLUDED_PARTS.intersection(path.parts):
                files.append(path)
    return sorted(files)


def physical_lines(path: Path) -> int:
    text = path.read_text(encoding="utf-8", errors="replace")
    if not text:
        return 0
    return text.count("\n") + (0 if text.endswith("\n") else 1)


def symbol_spans(path: Path) -> list[tuple[str, str, int]]:
    """(qualname, kind, span) for every function/method/class in the file."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return []
    spans: list[tuple[str, str, int]] = []

    def visit(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                qualname = f"{prefix}{child.name}"
                kind = "class" if isinstance(child, ast.ClassDef) else "function"
                spans.append((qualname, kind, child.end_lineno - child.lineno + 1))
                visit(child, f"{qualname}.")
            else:
                visit(child, prefix)

    visit(tree, "")
    return spans


def collect_violations(repo_root: Path, scan_roots: tuple[str, ...]) -> list[dict]:
    """Every measurement above a review threshold, sorted deterministically."""
    violations: list[dict] = []
    for path in iter_python_files(repo_root, scan_roots):
        rel = path.relative_to(repo_root).as_posix()
        test_file = is_test_path(rel)
        lines = physical_lines(path)
        if test_file and lines > TEST_MODULE_REVIEW:
            violations.append(_violation(rel, None, "physical_lines", lines, TEST_MODULE_REVIEW, "review"))
        elif not test_file and lines > PROD_MODULE_REVIEW:
            severity = "hard" if lines > PROD_MODULE_HARD else "review"
            threshold = PROD_MODULE_HARD if severity == "hard" else PROD_MODULE_REVIEW
            violations.append(_violation(rel, None, "physical_lines", lines, threshold, severity))
        if test_file:
            continue  # test-function target is advisory only
        for qualname, kind, span in symbol_spans(path):
            if kind == "class" and span > CLASS_REVIEW:
                violations.append(_violation(rel, qualname, "symbol_span", span, CLASS_REVIEW, "review"))
            elif kind == "function" and span > FUNCTION_REVIEW:
                severity = "hard" if span > FUNCTION_HARD else "review"
                threshold = FUNCTION_HARD if severity == "hard" else FUNCTION_REVIEW
                violations.append(_violation(rel, qualname, "symbol_span", span, threshold, severity))
    return sorted(violations, key=lambda v: (v["path"], v["symbol"] or "", v["metric"]))


def _violation(path: str, symbol: str | None, metric: str, value: int, threshold: int, severity: str) -> dict:
    return {"path": path, "symbol": symbol, "metric": metric,
            "value": value, "threshold": threshold, "severity": severity}


def load_baseline(path: Path) -> list[dict]:
    data = _load_json(path, "baseline")
    entries = data.get("entries")
    if data.get("version") != 1 or not isinstance(entries, list):
        raise ConfigError(f"baseline {path.name}: expected version 1 with an entries list")
    for entry in entries:
        if not isinstance(entry, dict) or not {"path", "metric", "value"} <= set(entry):
            raise ConfigError(f"baseline {path.name}: malformed entry {entry!r}")
    return entries


def load_exceptions(path: Path) -> tuple[list[dict], list[dict]]:
    data = _load_json(path, "exceptions")
    if data.get("version") != 1:
        raise ConfigError(f"exceptions {path.name}: expected version 1")
    exempt = data.get("exempt", [])
    exceptions = data.get("exceptions", [])
    for entry in exempt:
        if not isinstance(entry, dict) or entry.get("category") not in EXEMPT_CATEGORIES:
            raise ConfigError(f"exceptions {path.name}: exempt entry needs a valid category: {entry!r}")
    required = {"path", "metric", "limit", "reason", "scope", "owner", "validation"}
    for entry in exceptions:
        if not isinstance(entry, dict) or not required <= set(entry):
            raise ConfigError(f"exceptions {path.name}: malformed exception {entry!r}")
        if entry["scope"] == "temporary" and not entry.get("expires"):
            raise ConfigError(f"exceptions {path.name}: temporary exception needs expires: {entry!r}")
        if entry["scope"] not in ("temporary", "permanent"):
            raise ConfigError(f"exceptions {path.name}: scope must be temporary|permanent: {entry!r}")
    return exempt, exceptions


def _load_json(path: Path, label: str) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"{label} {path.name} is unreadable or not valid JSON: {exc}") from None
    if not isinstance(data, dict):
        raise ConfigError(f"{label} {path.name} must be a JSON object")
    return data


def _key(entry: dict) -> tuple[str, str | None, str]:
    return (entry["path"], entry.get("symbol"), entry["metric"])


def classify(violations: list[dict], baseline: list[dict], exempt: list[dict],
             exceptions: list[dict], today: date) -> tuple[list[str], int]:
    """Deterministic report lines plus exit code."""
    lines: list[str] = []
    failures = 0
    exempt_paths = {entry["path"] for entry in exempt}
    baseline_map = {_key(entry): entry for entry in baseline}
    exception_map = {_key(entry): entry for entry in exceptions}
    active = [v for v in violations if v["path"] not in exempt_paths]
    matched_baseline: set = set()
    matched_exception: set = set()

    for entry in sorted(exempt, key=lambda e: e["path"]):
        lines.append(f"EXEMPT {entry['path']} category=\"{entry['category']}\"")

    for violation in active:
        key = _key(violation)
        where = violation["path"] + (f"::{violation['symbol']}" if violation["symbol"] else "")
        facts = f"{violation['metric']}={violation['value']} threshold={violation['threshold']}"
        if key in exception_map:
            matched_exception.add(key)
            failures += _apply_exception(lines, exception_map[key], violation, where, facts, today)
        elif key in baseline_map:
            matched_baseline.add(key)
            failures += _apply_baseline(lines, baseline_map[key], violation, where, facts)
        else:
            lines.append(f"FAIL new-violation {where} {facts}")
            failures += 1

    for key, entry in sorted(baseline_map.items(), key=lambda item: item[0][0]):
        if key not in matched_baseline:
            where = entry["path"] + (f"::{entry['symbol']}" if entry.get("symbol") else "")
            lines.append(f"FAIL stale-baseline {where} {entry['metric']} recorded={entry['value']}")
            failures += 1
    for key, entry in sorted(exception_map.items(), key=lambda item: item[0][0]):
        if key not in matched_exception:
            where = entry["path"] + (f"::{entry['symbol']}" if entry.get("symbol") else "")
            lines.append(f"FAIL stale-exception {where} {entry['metric']} limit={entry['limit']}")
            failures += 1
    return lines, (1 if failures else 0)


def _apply_exception(lines: list[str], entry: dict, violation: dict, where: str,
                     facts: str, today: date) -> int:
    if entry["scope"] == "temporary" and date.fromisoformat(entry["expires"]) < today:
        lines.append(f"FAIL expired-exception {where} {facts} expired={entry['expires']}")
        return 1
    if violation["value"] > entry["limit"]:
        lines.append(f"FAIL exception-exceeded {where} {facts} limit={entry['limit']}")
        return 1
    lines.append(f"OK exception {where} {facts} limit={entry['limit']}")
    return 0


def _apply_baseline(lines: list[str], entry: dict, violation: dict, where: str, facts: str) -> int:
    if violation["value"] > entry["value"]:
        lines.append(f"FAIL increased-baseline {where} {facts} baseline={entry['value']}")
        return 1
    if violation["value"] < entry["value"]:
        lines.append(
            f"OK reduced-baseline {where} {facts} baseline={entry['value']} "
            "(update the baseline with --update-baseline)"
        )
        return 0
    lines.append(f"WARN unchanged-baseline {where} {facts}")
    return 0


def write_baseline(path: Path, violations: list[dict], exempt: list[dict]) -> None:
    exempt_paths = {entry["path"] for entry in exempt}
    entries = [
        {"path": v["path"], "symbol": v["symbol"], "metric": v["metric"], "value": v["value"]}
        for v in violations if v["path"] not in exempt_paths
    ]
    payload = {"version": 1, "entries": entries}
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8", newline="\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Repository size-policy checker")
    default_root = Path(__file__).resolve().parents[1]
    parser.add_argument("--repo-root", type=Path, default=default_root)
    parser.add_argument("--baseline", type=Path, default=None)
    parser.add_argument("--exceptions", type=Path, default=None)
    parser.add_argument("--scan-root", action="append", default=None,
                        help="repeatable; defaults to the configured scan roots")
    parser.add_argument("--update-baseline", action="store_true")
    parser.add_argument("--today", default=None, help="ISO date override for tests")
    args = parser.parse_args(argv)

    repo_root = args.repo_root.resolve()
    baseline_path = args.baseline or repo_root / "scripts" / "size_baseline.json"
    exceptions_path = args.exceptions or repo_root / "scripts" / "size_exceptions.json"
    scan_roots = tuple(args.scan_root) if args.scan_root else SCAN_ROOTS
    today = date.fromisoformat(args.today) if args.today else date.today()

    try:
        exempt, exceptions = load_exceptions(exceptions_path)
        if args.update_baseline and not baseline_path.exists():
            baseline = []  # first-run bootstrap: baseline is about to be written
        else:
            baseline = load_baseline(baseline_path)
    except ConfigError as exc:
        print(f"CONFIG-ERROR {exc}")
        return 2

    violations = collect_violations(repo_root, scan_roots)
    if args.update_baseline:
        write_baseline(baseline_path, violations, exempt)
        print(f"baseline written: {baseline_path.relative_to(repo_root).as_posix()}")
        return 0

    lines, code = classify(violations, baseline, exempt, exceptions, today)
    for line in lines:
        print(line)
    print(f"size-check: {'FAIL' if code else 'PASS'} "
          f"({sum(1 for l in lines if l.startswith('FAIL'))} failures, "
          f"{sum(1 for l in lines if l.startswith('WARN'))} warnings)")
    return code


if __name__ == "__main__":
    sys.exit(main())
