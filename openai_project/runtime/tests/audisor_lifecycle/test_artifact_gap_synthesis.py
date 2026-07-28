"""Gap-fix synthesis tests: found gaps become corrections evaluation actually sees."""
from __future__ import annotations

from pathlib import Path

from audisor.audisor_lifecycle.artifact_flow import run_artifact_lifecycle

from .flow_fixtures import (
    EVALUATION_PASSED,
    FIXTURE_DESIGN_OK,
    SUCCESS_CRITERIA_OK,
    ScriptedWorker,
    trigger,
)


class TestGapFixSynthesis:
    """The distinguishing behaviour: gap found -> gap autonomously fixed ->
    correction merged into the artifact evaluation actually sees."""

    def test_fix_is_merged_and_evaluation_sees_corrected_artifact(self, tmp_path: Path) -> None:
        original = "Deploy the widget service. Steps: build, test."
        corrected = (
            "Deploy the widget service. Steps: build, test, rollback via "
            "scripts/rollback.ps1 on failed health check."
        )
        worker = ScriptedWorker(
            {
                "gap_finding": {
                    "gaps": [
                        {
                            "gap": "no rollback procedure",
                            "category": "missing",
                            "evidence_basis": "scripts/rollback.ps1 exists in the repository",
                        }
                    ]
                },
                "gap_fixing": {
                    "fixes": [
                        {
                            "gap": "no rollback procedure",
                            "fix": "added rollback via scripts/rollback.ps1",
                            "evidence_basis": "scripts/rollback.ps1 exists in the repository",
                        }
                    ],
                    "unresolved": [],
                    "improved_artifact": corrected,
                },
                "evaluation": EVALUATION_PASSED,
                "success_criteria": SUCCESS_CRITERIA_OK,
                "fixture_design": FIXTURE_DESIGN_OK,
            }
        )
        result = run_artifact_lifecycle(trigger(content=original), worker, state_root=tmp_path)
        assert result["status"] == "improved"
        # gap_finding identified the omission
        assert result["gap_findings"][0]["gap"] == "no rollback procedure"
        # gap_fixing supplied the repository-grounded correction
        assert result["gap_fixes"][0]["evidence_basis"] == "scripts/rollback.ps1 exists in the repository"
        # the correction appears in the synthesised artifact
        assert "rollback via scripts/rollback.ps1" in result["improved_artifact"]
        assert result["improved_artifact"] != original
        # evaluation received the corrected version, not the original
        assert worker.payloads["evaluation"]["improved_artifact"] == corrected
        # so did success_criteria and fixture_design
        assert worker.payloads["success_criteria"]["improved_artifact"] == corrected
        assert worker.payloads["fixture_design"]["improved_artifact"] == corrected
        # and the gap is absent from any unresolved set
        assert "unresolved_gaps" not in result
