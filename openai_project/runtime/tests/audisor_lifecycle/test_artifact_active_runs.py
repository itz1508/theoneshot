"""Active-run guard tests for the lifecycle engine."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from audisor.audisor_lifecycle.artifact_flow import (
    _submission_digest,
    run_artifact_lifecycle,
)

from .flow_fixtures import happy_worker, trigger


class TestActiveRunGuard:
    def _marker(self, tmp_path: Path) -> Path:
        artifacts = tmp_path / "artifacts"
        artifacts.mkdir(parents=True, exist_ok=True)
        return artifacts / "artifact.test.running"

    def test_identical_submission_against_active_run_returns_its_identity(
        self, tmp_path: Path
    ) -> None:
        digest = _submission_digest(trigger()["content"], trigger())
        self._marker(tmp_path).write_text(
            json.dumps(
                {"lifecycle_run_id": "active123", "submission_digest": digest}
            ),
            encoding="utf-8",
        )
        worker = happy_worker()
        result = run_artifact_lifecycle(trigger(), worker, state_root=tmp_path)
        assert result["status"] == "error"
        assert result["stage"] == "active_run"
        assert result["lifecycle_run_id"] == "active123"
        # No second lifecycle started.
        assert worker.calls == []

    def test_stale_marker_is_a_crashed_run_and_lifecycle_proceeds(self, tmp_path: Path) -> None:
        digest = _submission_digest(trigger()["content"], trigger())
        marker = self._marker(tmp_path)
        marker.write_text(
            json.dumps({"lifecycle_run_id": "crashed", "submission_digest": digest}),
            encoding="utf-8",
        )
        stale = time.time() - 3600
        os.utime(marker, (stale, stale))
        result = run_artifact_lifecycle(trigger(), happy_worker(), state_root=tmp_path)
        assert result["status"] == "improved"
        # The finished run cleared its own marker.
        assert not marker.exists()

    def test_marker_is_cleared_after_a_normal_run(self, tmp_path: Path) -> None:
        run_artifact_lifecycle(trigger(), happy_worker(), state_root=tmp_path)
        assert not self._marker(tmp_path).exists()
