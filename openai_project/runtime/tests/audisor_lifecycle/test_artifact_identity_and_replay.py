"""Idempotent replay and runtime-derived identity tests for the lifecycle engine."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from audisor.audisor_lifecycle.artifact_flow import run_artifact_lifecycle

from .flow_fixtures import (
    GAP_FINDING_OK,
    GAP_FIXING_UNRESOLVED,
    ScriptedWorker,
    happy_worker,
    trigger,
)


class TestIdempotentReplay:
    def test_unchanged_resubmission_replays_without_new_lifecycle(self, tmp_path: Path) -> None:
        worker = happy_worker()
        first = run_artifact_lifecycle(trigger(), worker, state_root=tmp_path)
        assert first["status"] == "improved"
        assert first["submission_digest"]
        assert first["submission_disposition"] == "started"
        calls_after_first = list(worker.calls)
        second = run_artifact_lifecycle(trigger(), worker, state_root=tmp_path)
        # Same artifact_id + same content digest: persisted outcome replayed,
        # no stage ran again — and the replay is observable in the result.
        assert second == {**first, "submission_disposition": "replayed"}
        assert worker.calls == calls_after_first

    def test_changed_content_starts_a_new_lifecycle(self, tmp_path: Path) -> None:
        worker = happy_worker()
        first = run_artifact_lifecycle(trigger(), worker, state_root=tmp_path)
        second = run_artifact_lifecycle(
            trigger(content="Build a widget with tests, documentation, and telemetry."),
            worker,
            state_root=tmp_path,
        )
        assert second["status"] == "improved"
        assert second["submission_digest"] != first["submission_digest"]
        assert worker.calls.count("gap_finding") == 2

    def test_error_results_are_never_replayed(self, tmp_path: Path) -> None:
        class ExplodingWorker:
            def run_stage(self, stage_name: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
                raise RuntimeError("provider offline")

        errored = run_artifact_lifecycle(trigger(), ExplodingWorker(), state_root=tmp_path)
        assert errored["status"] == "error"
        # Retrying the identical submission runs a real lifecycle again.
        retried = run_artifact_lifecycle(trigger(), happy_worker(), state_root=tmp_path)
        assert retried["status"] == "improved"


class TestRuntimeDerivedIdentity:
    """Identity is runtime-derived, never agent-authored: canonical content
    digest drives revisions, submission digest drives replay, and every run
    gets a lifecycle_run_id plus timestamps."""

    def test_identity_fields_are_runtime_derived(self, tmp_path: Path) -> None:
        result = run_artifact_lifecycle(trigger(), happy_worker(), state_root=tmp_path)
        assert len(result["content_digest"]) == 64
        assert result["artifact_revision"] == 1
        assert result["lifecycle_run_id"]
        assert result["started_at"] <= result["completed_at"]
        assert result["submission_disposition"] == "started"
        # Explicit 0 | 1: no hidden meaning in absence.
        assert result["evaluation_repair_cycles"] == 0
        assert "evaluation_repair" not in result
        # None of these exist in the trigger contract the agent authors.
        assert {"content_digest", "artifact_revision", "lifecycle_run_id"}.isdisjoint(trigger())

    def test_line_ending_only_change_replays_not_reruns(self, tmp_path: Path) -> None:
        worker = happy_worker()
        base = "Build a widget.\nWith tests.\nAnd documentation."
        first = run_artifact_lifecycle(trigger(content=base), worker, state_root=tmp_path)
        calls_after_first = list(worker.calls)
        # Canonicalisation is line-ending normalization only: CRLF vs LF is
        # insignificant — same digest, replayed result.
        crlf = base.replace("\n", "\r\n")
        second = run_artifact_lifecycle(trigger(content=crlf), worker, state_root=tmp_path)
        assert second == {**first, "submission_disposition": "replayed"}
        assert worker.calls == calls_after_first

    def test_trailing_whitespace_is_significant_and_advances_revision(self, tmp_path: Path) -> None:
        first = run_artifact_lifecycle(trigger(), happy_worker(), state_root=tmp_path)
        # Markdown hard breaks, patches, and YAML make whitespace meaningful:
        # canonicalisation never strips it, so this is genuinely new content.
        second = run_artifact_lifecycle(
            trigger(content=trigger()["content"] + "  \n"),
            happy_worker(),
            state_root=tmp_path,
        )
        assert second["content_digest"] != first["content_digest"]
        assert second["artifact_revision"] == 2

    def test_changed_content_advances_the_revision(self, tmp_path: Path) -> None:
        first = run_artifact_lifecycle(trigger(), happy_worker(), state_root=tmp_path)
        second = run_artifact_lifecycle(
            trigger(content="Build a widget with tests, documentation, and telemetry."),
            happy_worker(),
            state_root=tmp_path,
        )
        assert first["artifact_revision"] == 1
        assert second["artifact_revision"] == 2
        assert second["content_digest"] != first["content_digest"]
        assert second["lifecycle_run_id"] != first["lifecycle_run_id"]

    def test_same_content_new_context_keeps_revision_with_new_run(self, tmp_path: Path) -> None:
        first = run_artifact_lifecycle(
            trigger(),
            ScriptedWorker({"gap_finding": GAP_FINDING_OK, "gap_fixing": GAP_FIXING_UNRESOLVED}),
            state_root=tmp_path,
        )
        assert first["status"] == "unresolved_gap"
        second = run_artifact_lifecycle(
            trigger(context="repo has pytest; deployment target is staging-eu"),
            happy_worker(),
            state_root=tmp_path,
        )
        # Recovery resubmission: content unchanged, so the revision holds —
        # but it is a genuinely new lifecycle run.
        assert second["status"] == "improved"
        assert second["artifact_revision"] == first["artifact_revision"] == 1
        assert second["lifecycle_run_id"] != first["lifecycle_run_id"]

    def test_original_submitted_text_is_retained_verbatim(self, tmp_path: Path) -> None:
        noisy = "Build a widget. \r\nWith tests.  \r\n"
        result = run_artifact_lifecycle(trigger(content=noisy), happy_worker(), state_root=tmp_path)
        # Digesting canonicalizes; the result never does.
        assert result["original_artifact"] == noisy
