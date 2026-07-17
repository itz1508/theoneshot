from __future__ import annotations

import json
import shutil

from aflow.evaluation.fixture_evaluator import evaluate_fixtures


def test_evaluator_fails_expected_data_leakage(fixture_root, tmp_path):
    source = fixture_root / "clean-plan"
    target = tmp_path / "clean-plan"
    shutil.copytree(source, target)
    manifest_path = target / "fixture-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["input_files"].append("expected/analysis-predicates.json")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    result = evaluate_fixtures(tmp_path)
    assert result["passed"] is False
    assert "expected-result data leaked" in result["cases"][0]["failures"][0]


def test_fixture_manifests_prohibit_external_side_effects(fixture_root):
    for manifest_path in fixture_root.glob("*/fixture-manifest.json"):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert manifest["safety"] == {
            "network": False, "docker": False, "git_mutation": False,
            "target_mutation": False, "subprocess": False,
        }
        assert not set(manifest["input_files"]) & set(manifest["expected_files"])


def test_evaluator_fails_blocking_and_readiness_mismatch_regression(fixture_root, tmp_path):
    source = fixture_root / "clean-plan"
    target = tmp_path / "clean-plan"
    shutil.copytree(source, target)
    expected_path = target / "expected" / "analysis-predicates.json"
    expected = json.loads(expected_path.read_text(encoding="utf-8"))
    expected["blocking"] = True
    expected["execution_ready"] = False
    expected_path.write_text(json.dumps(expected), encoding="utf-8")
    result = evaluate_fixtures(tmp_path)
    assert result["passed"] is False
    assert any("blocking" in failure or "execution_ready" in failure for failure in result["cases"][0]["failures"])
