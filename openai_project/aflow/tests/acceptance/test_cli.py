from __future__ import annotations

import json

from aflow.cli import (
    EXIT_BLOCKING, EXIT_FIXTURE_FAILURE, EXIT_OK, EXIT_SCHEMA_INVALID, EXIT_UNPROVEN, main,
)


def _assemble(fixture):
    inputs = fixture / "input"
    return {
        "schema_version": "1.0.0", "analysis_id": "analysis.fixture",
        "success_definition": json.loads((inputs / "success-definition.json").read_text(encoding="utf-8")),
        "plan": json.loads((inputs / "plan.json").read_text(encoding="utf-8")),
        "authority_evidence": json.loads((inputs / "authority-evidence.json").read_text(encoding="utf-8")),
        "repository_evidence": json.loads((inputs / "repository-evidence.json").read_text(encoding="utf-8")),
        "baseline": json.loads((inputs / "baseline.json").read_text(encoding="utf-8")),
        "evidence": json.loads((inputs / "evidence.json").read_text(encoding="utf-8")),
    }


def test_analyze_cli_exit_contracts(fixture_root, tmp_path, capsys):
    cases = [
        ("clean-plan", EXIT_OK),
        ("02-missing-schema-field", EXIT_SCHEMA_INVALID),
        ("03-unsupported-assumption", EXIT_BLOCKING),
    ]
    for name, expected in cases:
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(_assemble(fixture_root / name)), encoding="utf-8")
        assert main(["analyze", str(path)]) == expected
        capsys.readouterr()


def test_close_evaluate_fixtures_result_and_demo_cli_contracts(fixture_root, capsys):
    assert main(["close", str(fixture_root / "effective-revision" / "input" / "closure-request.json")]) == EXIT_OK
    capsys.readouterr()
    assert main(["close", str(fixture_root / "ineffective-revision" / "input" / "closure-request.json")]) == EXIT_BLOCKING
    capsys.readouterr()
    assert main(["evaluate-result", str(fixture_root / "05-fully-proven" / "input" / "build-result.json")]) == EXIT_OK
    capsys.readouterr()
    assert main(["evaluate-result", str(fixture_root / "output-quality-fails" / "input" / "build-result.json")]) == EXIT_UNPROVEN
    capsys.readouterr()
    assert main(["evaluate-fixtures", str(fixture_root)]) == EXIT_OK
    capsys.readouterr()
    assert main(["demo"]) == EXIT_OK

