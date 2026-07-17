from __future__ import annotations

import json
from pathlib import Path

from aflow.cli import demo
from aflow.evaluation.fixture_evaluator import evaluate_fixtures


def test_complete_corrected_fixture_suite(fixture_root):
    result = evaluate_fixtures(fixture_root)
    assert result["fixture_count"] == 16
    assert result["passed"] is True, result["cases"]
    assert all(metric["accuracy"] == 1.0 for metric in result["metrics"].values())
    retry = fixture_root / "04-invented-concern-rejected" / "input"
    success = json.loads((retry / "success-definition.json").read_text(encoding="utf-8"))
    combined = " ".join([success["success_statement"], success["requirements"][0]["observable_outcome"]])
    assert "maximum four total calls" in combined
    assert "1s, 2s, and 4s" in combined


def test_exact_nine_step_demo_matches_golden():
    result = demo()
    expected = json.loads((Path(__file__).parents[1] / "golden" / "demo-summary.json").read_text(encoding="utf-8"))
    assert len(result["steps"]) == expected["step_count"]
    assert result["steps"][1]["decision"] == expected["step_2_decision"]
    assert result["steps"][2]["decision"] == expected["step_3_decision"]
    assert result["steps"][3]["statuses"] == [expected["step_4_status"]]
    assert result["steps"][6]["decision"] == expected["step_7_decision"]
    assert result["steps"][8]["decision"] == expected["step_9_decision"]
