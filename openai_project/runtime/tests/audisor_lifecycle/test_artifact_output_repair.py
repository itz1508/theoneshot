"""Recorded output-repair tests for the lifecycle engine."""
from __future__ import annotations

from pathlib import Path

from audisor.audisor_lifecycle.artifact_flow import (
    read_last_result,
    run_artifact_lifecycle,
)

from .flow_fixtures import happy_worker, trigger


class TestRecordedRepair:
    def test_unknown_fields_repaired_and_recorded_in_result(self, tmp_path: Path) -> None:
        worker = happy_worker()
        worker.outputs["gap_finding"] = {
            "gaps": [{"gap": "no rollout step", "category": "missing", "severity": "high"}],
            "confidence": 0.9,
        }
        result = run_artifact_lifecycle(trigger(), worker, state_root=tmp_path)
        assert result["status"] == "improved"
        # The consumed output is schema-clean...
        assert "severity" not in result["gap_findings"][0]
        # ...and every removal is explicit, with the original value retained.
        assert result["output_repaired"] is True
        assert result["repair_policy"] == "recorded-removal-v1"
        (repair,) = result["output_repairs"]
        assert repair["stage"] == "gap_finding"
        removed = {entry["path"]: entry["removed"] for entry in repair["removed_fields"]}
        assert removed == {"$.confidence": 0.9, "$.gaps[0].severity": "high"}
        # The persisted result carries the same repair record.
        persisted = read_last_result("artifact.test", state_root=tmp_path)
        assert persisted["output_repairs"] == result["output_repairs"]

    def test_clean_outputs_carry_no_repair_fields(self, tmp_path: Path) -> None:
        result = run_artifact_lifecycle(trigger(), happy_worker(), state_root=tmp_path)
        assert result["status"] == "improved"
        assert "output_repaired" not in result
        assert "output_repairs" not in result
        assert "repair_policy" not in result

    def test_repair_never_supplies_missing_required_fields(self, tmp_path: Path) -> None:
        worker = happy_worker()
        worker.outputs["gap_fixing"] = {
            "fixes": [{"gap": "no rollout step", "fix": "added rollout step"}],
            "improved_artifact": "Build a widget with tests, documentation, and rollout.",
            "extra": "chatter",
        }
        result = run_artifact_lifecycle(trigger(), worker, state_root=tmp_path)
        # Removing "extra" cannot fabricate the missing required field:
        # validation still fails and the stage errors out — with the
        # attempted repair on record.
        assert result["status"] == "error"
        assert result["stage"] == "gap_fixing"
        assert "'unresolved' is a required property" in result["detail"]
        assert result["output_repaired"] is True
        assert result["output_repairs"][0]["removed_fields"][0]["path"] == "$.extra"

    def test_authoritative_stages_are_strict_as_emitted(self, tmp_path: Path) -> None:
        worker = happy_worker()
        worker.outputs["evaluation"] = {
            "passed": True,
            "summary": "coherent",
            "findings": [],
            "confidence": 0.9,
        }
        result = run_artifact_lifecycle(trigger(), worker, state_root=tmp_path)
        # Evaluation (like success_criteria and fixture_design) admits no
        # repair: an unknown field is a bounded stage error, never a
        # normalized pass.
        assert result["status"] == "error"
        assert result["stage"] == "evaluation"
        assert "'confidence'" in result["detail"]
        assert "output_repaired" not in result

    def test_excessive_repair_is_a_malformed_output_error(self, tmp_path: Path) -> None:
        worker = happy_worker()
        chatter = {f"extra_{i}": i for i in range(9)}
        worker.outputs["gap_finding"] = {
            "gaps": [{"gap": "no rollout step"}],
            **chatter,
        }
        result = run_artifact_lifecycle(trigger(), worker, state_root=tmp_path)
        assert result["status"] == "error"
        assert result["stage"] == "gap_finding"
        assert "ceiling" in result["detail"]
        assert "output_repaired" not in result
