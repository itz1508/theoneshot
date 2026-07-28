"""Bounded evaluation-repair cycle tests for the lifecycle engine."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from audisor.audisor_lifecycle.artifact_flow import run_artifact_lifecycle

from .flow_fixtures import (
    EVALUATION_FAILED,
    EVALUATION_PASSED,
    FIXTURE_DESIGN_OK,
    GAP_FINDING_OK,
    GAP_FIXING_RESOLVED,
    SUCCESS_CRITERIA_OK,
    ScriptedWorker,
    trigger,
)


class TestEvaluationRepairCycle:
    """One bounded gap_fixing/evaluation cycle for evaluation-discovered
    deficiencies; the digest rule, in code, decides progress."""

    REFIX_IMPROVED = "Build a widget with tests, documentation, rollout, and aligned API spec."

    def refix_output(self) -> dict[str, Any]:
        return {
            "fixes": [{"gap": "widget spec contradicts existing API", "fix": "aligned spec with API"}],
            "unresolved": [],
            "improved_artifact": self.REFIX_IMPROVED,
        }

    def test_cycle_recovers_and_produces_improved(self, tmp_path: Path) -> None:
        worker = ScriptedWorker(
            {
                "gap_finding": GAP_FINDING_OK,
                "gap_fixing": [GAP_FIXING_RESOLVED, self.refix_output()],
                "evaluation": [EVALUATION_FAILED, EVALUATION_PASSED],
                "success_criteria": SUCCESS_CRITERIA_OK,
                "fixture_design": FIXTURE_DESIGN_OK,
            }
        )
        result = run_artifact_lifecycle(trigger(), worker, state_root=tmp_path)
        assert result["status"] == "improved"
        assert result["evaluation_repair_cycles"] == 1
        assert result["improved_artifact"] == self.REFIX_IMPROVED
        # Both rounds of fixes are on the record.
        assert result["gap_fixes"] == (
            GAP_FIXING_RESOLVED["fixes"] + self.refix_output()["fixes"]
        )
        assert worker.calls == [
            "gap_finding",
            "gap_fixing",
            "evaluation",
            "gap_fixing",
            "evaluation",
            "success_criteria",
            "fixture_design",
        ]
        # The re-fix received ONLY the evaluation-discovered deficiencies and
        # the current improved artifact — not the original finding set —
        # plus the structured refix contract derived in code.
        refix_payload = worker.history[3][1]
        assert refix_payload["gaps"] == [{"gap": "widget spec contradicts existing API"}]
        assert refix_payload["artifact"] == GAP_FIXING_RESOLVED["improved_artifact"]
        assert refix_payload["previously_fixed"] == GAP_FIXING_RESOLVED["fixes"]
        expected_contract = [
            {
                "finding_id": "evaluation.1",
                "deficiency": "widget spec contradicts existing API",
                "evidence": [],
                "required_correction": "align spec with API",
                "success_condition": "spec matches the API contract",
            }
        ]
        assert refix_payload["refix_contract"] == expected_contract
        # The second evaluation verifies the contract, not just any change:
        # a changed digest alone is never success.
        second_eval_payload = worker.history[4][1]
        assert second_eval_payload["verify_corrected"] == expected_contract
        # The first evaluation carries no verification clause.
        assert "verify_corrected" not in worker.history[2][1]
        # The bounded cycle leaves evidence, not just a counter: structured
        # findings, digest comparison, re-fix output, and the final verdict.
        repair = result["evaluation_repair"]
        assert repair["findings"] == expected_contract
        assert repair["before_content_digest"] != repair["after_content_digest"]
        assert repair["gap_fixes"] == self.refix_output()["fixes"]
        assert repair["final_evaluation"]["passed"] is True
        # Downstream stages consumed the re-fixed artifact.
        assert worker.payloads["success_criteria"]["improved_artifact"] == self.REFIX_IMPROVED

    def test_second_evaluation_failure_is_unresolved_with_null_downstream(self, tmp_path: Path) -> None:
        worker = ScriptedWorker(
            {
                "gap_finding": GAP_FINDING_OK,
                "gap_fixing": [GAP_FIXING_RESOLVED, self.refix_output()],
                "evaluation": [EVALUATION_FAILED, EVALUATION_FAILED],
                "success_criteria": SUCCESS_CRITERIA_OK,
            }
        )
        result = run_artifact_lifecycle(trigger(), worker, state_root=tmp_path)
        assert result["status"] == "unresolved_gap"
        assert result["unresolved_gaps"][0]["why_unresolved"] == (
            "discovered during evaluation; unresolved after one repair cycle"
        )
        assert result["evaluation"] is None
        assert result["success_criteria"] is None
        assert result["fixture_cases"] is None
        assert result["evaluation_repair_cycles"] == 1
        # The failed final evaluation is preserved as diagnostic evidence:
        # the nulled top-level evaluation never erases why this failed.
        repair = result["evaluation_repair"]
        assert repair["final_evaluation"]["passed"] is False
        assert repair["final_evaluation"]["findings"] == EVALUATION_FAILED["findings"]
        # Bounded: exactly one re-fix and one re-evaluation, nothing after.
        assert worker.calls == [
            "gap_finding",
            "gap_fixing",
            "evaluation",
            "gap_fixing",
            "evaluation",
        ]

    def test_refix_unresolved_entries_hit_the_barrier(self, tmp_path: Path) -> None:
        refix = self.refix_output()
        refix["fixes"] = []
        refix["unresolved"] = [
            {
                "gap": "widget spec contradicts existing API",
                "why_unresolved": "API contract not in the provided context",
                "required_to_resolve": ["API contract document"],
                "successful_resolution": "spec matches the API contract",
            }
        ]
        worker = ScriptedWorker(
            {
                "gap_finding": GAP_FINDING_OK,
                "gap_fixing": [GAP_FIXING_RESOLVED, refix],
                "evaluation": EVALUATION_FAILED,
            }
        )
        result = run_artifact_lifecycle(trigger(), worker, state_root=tmp_path)
        assert result["status"] == "unresolved_gap"
        assert result["unresolved_gaps"] == refix["unresolved"]
        assert result["evaluation"] is None
        assert worker.calls == ["gap_finding", "gap_fixing", "evaluation", "gap_fixing"]

    def test_repair_cycle_making_no_digest_progress_is_unresolved(
        self, tmp_path: Path
    ) -> None:
        """Fixture 15: the second gap-fixing returns the same canonical
        content. In code, not model opinion, that is no progress — the
        digest rule fires before the second evaluation ever runs."""
        refix = {
            "fixes": [{"gap": "widget spec contradicts existing API", "fix": "tweaked wording"}],
            "unresolved": [],
            # Same canonical artifact as the first gap-fixing output —
            # digest unchanged, so the progress rule must trip.
            "improved_artifact": GAP_FIXING_RESOLVED["improved_artifact"],
        }
        worker = ScriptedWorker(
            {
                "gap_finding": GAP_FINDING_OK,
                "gap_fixing": [GAP_FIXING_RESOLVED, refix],
                "evaluation": EVALUATION_FAILED,
            }
        )
        result = run_artifact_lifecycle(trigger(), worker, state_root=tmp_path)
        assert result["status"] == "unresolved_gap"
        assert result["unresolved_gaps"][0]["why_unresolved"].endswith(
            "repair cycle made no progress"
        )
        # The second evaluation never runs — no progress means no point.
        assert result["evaluation"] is None
        assert result["success_criteria"] is None
        assert result["fixture_cases"] is None
        assert worker.calls == ["gap_finding", "gap_fixing", "evaluation", "gap_fixing"]

