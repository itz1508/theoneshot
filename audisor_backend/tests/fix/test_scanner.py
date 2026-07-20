from pathlib import Path

from audisor_backend.scanning.scanner import DeterministicScanner, ScanConfig


def write(root: Path, name: str, text: str) -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_scanner_finds_normalized_actionable_issues_without_originating_id(tmp_path):
    write(tmp_path, "a.py", "import missing_pkg\nvalue = eval('1')\n")
    write(tmp_path, "b.py", "import missing_pkg\nvalue = eval('1')\n")
    write(tmp_path, "requirements.txt", "requests==1\n")
    write(tmp_path, "package.json", '{"dependencies":{"requests":"1"}}')
    report = DeterministicScanner().scan(tmp_path)
    types = {finding.type for finding in report.findings}
    assert "dependency.unresolved" in types
    assert "security.unsafe_command" in types
    assert "structure.duplicate_implementation" in types
    assert all(not hasattr(finding, "originating_finding_id") for finding in report.findings)
    assert set(report.affected_items) == {finding.file for finding in report.findings}


def test_scanner_reports_syntax_and_configuration_evidence(tmp_path):
    write(tmp_path, "broken.py", "def broken(:\n")
    write(tmp_path, "settings.json", "{broken")
    report = DeterministicScanner().scan(tmp_path)
    by_type = {finding.type: finding for finding in report.findings}
    assert by_type["correctness.syntax_error"].evidence["repro"] == "python -m py_compile broken.py"
    assert by_type["configuration.invalid_configuration"].evidence["repro"] == "python -m json.tool settings.json"


def test_scanner_is_deterministic_and_excludes_generated_directories(tmp_path):
    write(tmp_path, "controller.py", "def run():\n    return 1\n")
    write(tmp_path, "runner.py", "def run():\n    return 1\n")
    write(tmp_path, "__pycache__/ignored.py", "eval('bad')")
    write(tmp_path, "snapshot/copied.py", "eval('bad')")
    scanner = DeterministicScanner(ScanConfig())
    first = scanner.scan(tmp_path)
    second = scanner.scan(tmp_path)
    assert first == second
    assert all("__pycache__" not in finding.file for finding in first.findings)
    assert all("snapshot/" not in finding.file for finding in first.findings)
    assert any(finding.type == "authority.competing_authority_path" for finding in first.findings)


def test_scanner_does_not_write_target(tmp_path):
    write(tmp_path, "clean.py", "value = 1\n")
    before = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*"))
    DeterministicScanner().scan(tmp_path)
    after = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*"))
    assert before == after


def test_scanner_supports_contract_and_injected_test_failures(tmp_path):
    write(tmp_path, "contract.json", '{"version": "1"}')
    config = ScanConfig(
        test_commands=(("pytest", "tests"),),
        test_runner=lambda command, root: (1, "one failed", ""),
        contract_requirements=(("contract.json", ("version", "decision")),),
    )
    report = DeterministicScanner(config).scan(tmp_path)
    types = {finding.type for finding in report.findings}
    assert "schema.contract_violation" in types
    assert "test.regression_failure" in types


def test_import_resolution_handles_stdlib_local_relative_namespace_and_external(tmp_path):
    write(tmp_path, "src/pkg/main.py", "import json\nimport pkg.sibling\nfrom . import sibling\nimport pytest\nimport absent_package\n")
    write(tmp_path, "src/pkg/sibling.py", "value = 1\n")
    config = ScanConfig(repository_root=str(tmp_path), source_roots=("src",))
    findings = DeterministicScanner(config).scan(tmp_path / "src" / "pkg").findings
    unresolved = [finding for finding in findings if finding.type == "dependency.unresolved"]
    assert len(unresolved) == 1
    assert unresolved[0].evidence["module"] == "absent_package"
    assert unresolved[0].evidence["resolver_state"] == "missing"


def test_dependency_declarations_distinguish_redundancy_from_conflict(tmp_path):
    write(tmp_path, "requirements.txt", "requests==2.31.0\n")
    write(tmp_path, "pyproject.toml", "requests = \"==2.31.0\"\n")
    report = DeterministicScanner().scan(tmp_path)
    assert {f.type for f in report.findings} == {"dependency.duplicate_declaration"}

    write(tmp_path, "pyproject.toml", "requests = \"==2.28.0\"\n")
    report = DeterministicScanner().scan(tmp_path)
    conflicts = [f for f in report.findings if f.type == "dependency.conflicting_constraints"]
    assert len(conflicts) == 1
    assert {item["constraint"] for item in conflicts[0].evidence["declarations"]} == {"==2.31.0", "==2.28.0"}


def test_interface_methods_are_not_reported_as_duplicate_implementations(tmp_path):
    write(tmp_path, "one.py", "from typing import Protocol\nclass Worker(Protocol):\n    def execute(self): ...\n")
    write(tmp_path, "two.py", "from typing import Protocol\nclass Other(Protocol):\n    def execute(self): ...\n")
    report = DeterministicScanner().scan(tmp_path)
    assert "structure.overlapping_implementation" not in {finding.type for finding in report.findings}
    assert not any(finding.evidence.get("symbol") == "execute" for finding in report.findings)
