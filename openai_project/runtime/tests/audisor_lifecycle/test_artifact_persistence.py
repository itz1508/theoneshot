"""Persisted-state integrity and result-persistence tests for the lifecycle engine."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from audisor.audisor_lifecycle.artifact_flow import (
    PersistedResultError,
    read_last_result,
    run_artifact_lifecycle,
)

from .flow_fixtures import (
    GAP_FINDING_OK,
    GAP_FIXING_UNRESOLVED,
    ScriptedWorker,
    happy_worker,
    trigger,
)


class TestPersistedStateIntegrity:
    def _newest_path(self, tmp_path: Path) -> Path:
        artifacts = tmp_path / "artifacts"
        artifacts.mkdir(parents=True, exist_ok=True)
        # "9..." sorts after every real UTC timestamp, so this is newest.
        return artifacts / "artifact.test-99991231T000000000000Z.json"

    def test_corrupt_persisted_result_is_a_bounded_error(self, tmp_path: Path) -> None:
        bad = self._newest_path(tmp_path)
        bad.write_text("{not json", encoding="utf-8")
        worker = happy_worker()
        result = run_artifact_lifecycle(trigger(), worker, state_root=tmp_path)
        assert result["status"] == "error"
        assert result["stage"] == "persisted_state"
        assert "corrupt" in result["detail"]
        # Structured diagnostic, not just prose: the damaged file is named.
        assert result["state_path"] == str(bad)
        assert result["failure"] == "corrupt"
        # Never silently treated as completed; no lifecycle ran.
        assert worker.calls == []

    def test_incomplete_persisted_result_is_a_bounded_error(self, tmp_path: Path) -> None:
        bad = self._newest_path(tmp_path)
        bad.write_text(
            json.dumps({"artifact_id": "artifact.test"}), encoding="utf-8"
        )
        result = run_artifact_lifecycle(trigger(), happy_worker(), state_root=tmp_path)
        assert result["status"] == "error"
        assert result["stage"] == "persisted_state"
        assert "incomplete" in result["detail"]
        assert result["state_path"] == str(bad)
        assert result["failure"] == "incomplete"

    def test_read_last_result_raises_on_corrupt_state(self, tmp_path: Path) -> None:
        self._newest_path(tmp_path).write_text("{not json", encoding="utf-8")
        with pytest.raises(PersistedResultError):
            read_last_result("artifact.test", state_root=tmp_path)


class TestResultPersistence:
    def test_result_persisted_to_state_dir(self, tmp_path: Path) -> None:
        result = run_artifact_lifecycle(trigger(), happy_worker(), state_root=tmp_path)
        files = list((tmp_path / "artifacts").glob("artifact.test-*.json"))
        assert len(files) == 1
        stored = json.loads(files[0].read_text(encoding="utf-8"))
        assert stored == result

    def test_read_last_result_returns_latest_cycle(self, tmp_path: Path) -> None:
        run_artifact_lifecycle(
            trigger(), ScriptedWorker({"gap_finding": GAP_FINDING_OK, "gap_fixing": GAP_FIXING_UNRESOLVED}), state_root=tmp_path
        )
        time.sleep(0.01)
        # Recovery pattern: the resubmission supplies the missing input via
        # changed context, so it is a new run, not an idempotent replay.
        second = run_artifact_lifecycle(
            trigger(context="repo has pytest; deployment target is staging-eu"),
            happy_worker(),
            state_root=tmp_path,
        )
        latest = read_last_result("artifact.test", state_root=tmp_path)
        assert latest == second
        assert latest["status"] == "improved"

    def test_read_last_result_unknown_artifact_is_none(self, tmp_path: Path) -> None:
        assert read_last_result("artifact.never-submitted", state_root=tmp_path) is None
