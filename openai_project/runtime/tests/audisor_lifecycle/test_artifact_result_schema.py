"""Result-schema conformance tests: every emitted result shape validates."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

import pytest

from audisor.audisor_lifecycle.artifact_flow import (
    _submission_digest,
    run_artifact_lifecycle,
)

from .flow_fixtures import (
    EVALUATION_FAILED,
    EVALUATION_PASSED,
    FIXTURE_DESIGN_OK,
    GAP_FINDING_OK,
    GAP_FIXING_RESOLVED,
    GAP_FIXING_UNRESOLVED,
    SUCCESS_CRITERIA_OK,
    ScriptedWorker,
    happy_worker,
    trigger,
)


class TestResultSchemaConformance:
    """Every emitted result shape validates against the published result
    schema — the schema enforces the lifecycle contract, not just describes
    it (identity required, repair trio atomic, repair-cycle evidence
    required when a cycle ran, structurally distinct error variants)."""

    @pytest.fixture(scope="class")
    def validator(self):  # noqa: ANN201 - jsonschema validator instance
        import jsonschema

        schema_path = (
            Path(__file__).resolve().parents[3]
            / "schemas"
            / "aflow-artifact-result.schema.json"
        )
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        return jsonschema.Draft202012Validator(schema)

    def test_improved_result_conforms(self, tmp_path: Path, validator) -> None:
        result = run_artifact_lifecycle(trigger(), happy_worker(), state_root=tmp_path)
        validator.validate(result)

    def test_replayed_result_conforms(self, tmp_path: Path, validator) -> None:
        run_artifact_lifecycle(trigger(), happy_worker(), state_root=tmp_path)
        replayed = run_artifact_lifecycle(trigger(), happy_worker(), state_root=tmp_path)
        assert replayed["submission_disposition"] == "replayed"
        validator.validate(replayed)

    def test_unresolved_barrier_result_conforms(self, tmp_path: Path, validator) -> None:
        result = run_artifact_lifecycle(
            trigger(),
            ScriptedWorker({"gap_finding": GAP_FINDING_OK, "gap_fixing": GAP_FIXING_UNRESOLVED}),
            state_root=tmp_path,
        )
        assert result["status"] == "unresolved_gap"
        validator.validate(result)

    def test_repair_cycle_improved_result_conforms(self, tmp_path: Path, validator) -> None:
        refix = {
            "fixes": [{"gap": "widget spec contradicts existing API", "fix": "aligned"}],
            "unresolved": [],
            "improved_artifact": "Build a widget with tests, documentation, rollout, and aligned API spec.",
        }
        result = run_artifact_lifecycle(
            trigger(),
            ScriptedWorker(
                {
                    "gap_finding": GAP_FINDING_OK,
                    "gap_fixing": [GAP_FIXING_RESOLVED, refix],
                    "evaluation": [EVALUATION_FAILED, EVALUATION_PASSED],
                    "success_criteria": SUCCESS_CRITERIA_OK,
                    "fixture_design": FIXTURE_DESIGN_OK,
                }
            ),
            state_root=tmp_path,
        )
        assert result["evaluation_repair_cycles"] == 1
        validator.validate(result)

    def test_second_evaluation_failure_result_conforms(self, tmp_path: Path, validator) -> None:
        refix = {
            "fixes": [{"gap": "widget spec contradicts existing API", "fix": "aligned"}],
            "unresolved": [],
            "improved_artifact": "Build a widget with tests, documentation, rollout, and aligned API spec.",
        }
        result = run_artifact_lifecycle(
            trigger(),
            ScriptedWorker(
                {
                    "gap_finding": GAP_FINDING_OK,
                    "gap_fixing": [GAP_FIXING_RESOLVED, refix],
                    "evaluation": [EVALUATION_FAILED, EVALUATION_FAILED],
                }
            ),
            state_root=tmp_path,
        )
        assert result["status"] == "unresolved_gap"
        validator.validate(result)

    def test_skip_result_conforms(self, tmp_path: Path, validator) -> None:
        result = run_artifact_lifecycle(trigger(status="collecting"), happy_worker(), state_root=tmp_path)
        assert result["status"] == "skip"
        validator.validate(result)

    def test_stage_error_result_conforms(self, tmp_path: Path, validator) -> None:
        class Exploding:
            def run_stage(self, stage_name: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
                raise RuntimeError("provider offline")

        result = run_artifact_lifecycle(trigger(), Exploding(), state_root=tmp_path)
        assert result["status"] == "error"
        validator.validate(result)

    def test_persisted_state_error_conforms(self, tmp_path: Path, validator) -> None:
        artifacts = tmp_path / "artifacts"
        artifacts.mkdir(parents=True, exist_ok=True)
        (artifacts / "artifact.test-99991231T000000000000Z.json").write_text(
            "{not json", encoding="utf-8"
        )
        result = run_artifact_lifecycle(trigger(), happy_worker(), state_root=tmp_path)
        assert result["stage"] == "persisted_state"
        validator.validate(result)

    def test_active_run_error_conforms(self, tmp_path: Path, validator) -> None:
        artifacts = tmp_path / "artifacts"
        artifacts.mkdir(parents=True, exist_ok=True)
        digest = _submission_digest(trigger()["content"], trigger())
        (artifacts / "artifact.test.running").write_text(
            json.dumps({"lifecycle_run_id": "active123", "submission_digest": digest}),
            encoding="utf-8",
        )
        result = run_artifact_lifecycle(trigger(), happy_worker(), state_root=tmp_path)
        assert result["stage"] == "active_run"
        validator.validate(result)
