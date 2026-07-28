"""Trigger contract and unresolved-gap barrier tests for the lifecycle engine."""
from __future__ import annotations

from pathlib import Path

from audisor.audisor_lifecycle.artifact_flow import run_artifact_lifecycle

from .flow_fixtures import (
    EVALUATION_FAILED,
    EVALUATION_PASSED,
    GAP_FINDING_OK,
    GAP_FIXING_RESOLVED,
    GAP_FIXING_UNRESOLVED,
    SUCCESS_CRITERIA_OK,
    ScriptedWorker,
    happy_worker,
    trigger,
)


class TestTriggerContract:
    def test_non_draft_complete_status_skips_without_worker(self, tmp_path: Path) -> None:
        worker = happy_worker()
        result = run_artifact_lifecycle(
            trigger(status="collecting"), worker, state_root=tmp_path
        )
        assert result["status"] == "skip"
        assert worker.calls == []

    def test_empty_content_skips_without_worker(self, tmp_path: Path) -> None:
        worker = happy_worker()
        result = run_artifact_lifecycle(trigger(content="   "), worker, state_root=tmp_path)
        assert result["status"] == "skip"
        assert worker.calls == []

    def test_missing_artifact_id_skips(self, tmp_path: Path) -> None:
        worker = happy_worker()
        result = run_artifact_lifecycle(trigger(artifact_id=""), worker, state_root=tmp_path)
        assert result["status"] == "skip"
        assert worker.calls == []


class TestUnresolvedGapBarrier:
    def test_unresolved_gap_ends_lifecycle_with_null_downstream(self, tmp_path: Path) -> None:
        worker = ScriptedWorker(
            {
                "gap_finding": GAP_FINDING_OK,
                "gap_fixing": GAP_FIXING_UNRESOLVED,
                # evaluation output present but must never be requested
                "evaluation": EVALUATION_PASSED,
            }
        )
        result = run_artifact_lifecycle(trigger(), worker, state_root=tmp_path)
        assert result["status"] == "unresolved_gap"
        assert result["evaluation"] is None
        assert result["success_criteria"] is None
        assert result["fixture_cases"] is None
        gap = result["unresolved_gaps"][0]
        assert set(gap) == {"gap", "why_unresolved", "required_to_resolve", "successful_resolution"}
        # Ordering barrier proof: evaluation stage never invoked
        assert worker.calls == ["gap_finding", "gap_fixing"]

    def test_failed_evaluation_without_progress_maps_to_unresolved_gap_shape(self, tmp_path: Path) -> None:
        # Static gap_fixing output: the bounded repair cycle runs once but the
        # artifact digest does not change, so the code-owned no-progress rule
        # ends the lifecycle as unresolved_gap.
        worker = ScriptedWorker(
            {
                "gap_finding": GAP_FINDING_OK,
                "gap_fixing": GAP_FIXING_RESOLVED,
                "evaluation": EVALUATION_FAILED,
                "success_criteria": SUCCESS_CRITERIA_OK,
            }
        )
        result = run_artifact_lifecycle(trigger(), worker, state_root=tmp_path)
        assert result["status"] == "unresolved_gap"
        assert result["unresolved_gaps"][0]["why_unresolved"] == (
            "discovered during evaluation; repair cycle made no progress"
        )
        assert result["evaluation"] is None
        assert result["success_criteria"] is None
        assert result["fixture_cases"] is None
        assert result["evaluation_repair_cycles"] == 1
        # No-progress is visible in the evidence: identical digests, and the
        # final evaluation never ran.
        repair = result["evaluation_repair"]
        assert repair["before_content_digest"] == repair["after_content_digest"]
        assert repair["final_evaluation"] is None
        assert worker.calls == ["gap_finding", "gap_fixing", "evaluation", "gap_fixing"]
