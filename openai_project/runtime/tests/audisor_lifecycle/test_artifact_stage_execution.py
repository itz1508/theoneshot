"""Happy-path, error-contract, evidence-basis, and worker-parsing tests."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Mapping

import pytest

from audisor.audisor_lifecycle.artifact_flow import (
    LocalStageWorker,
    read_last_result,
    run_artifact_lifecycle,
)

from .flow_fixtures import (
    GAP_FINDING_OK,
    GAP_FIXING_RESOLVED,
    ScriptedWorker,
    happy_worker,
    trigger,
)


class TestHappyPath:
    def test_improved_result_contains_all_sections_in_order(self, tmp_path: Path) -> None:
        worker = happy_worker()
        result = run_artifact_lifecycle(trigger(), worker, state_root=tmp_path)
        assert result["status"] == "improved"
        assert result["original_artifact"] == trigger()["content"]
        assert result["gap_findings"] == GAP_FINDING_OK["gaps"]
        assert result["gap_fixes"] == GAP_FIXING_RESOLVED["fixes"]
        assert result["improved_artifact"] == GAP_FIXING_RESOLVED["improved_artifact"]
        assert result["evaluation"]["passed"] is True
        assert result["success_criteria"][0]["validation"] == "uv run pytest exit code 0"
        assert result["fixture_cases"][0]["name"] == "happy path"
        # Stage invocation order asserted
        assert worker.calls == [
            "gap_finding",
            "gap_fixing",
            "evaluation",
            "success_criteria",
            "fixture_design",
        ]


class TestErrorContract:
    def test_worker_exception_returns_error_with_stage(self, tmp_path: Path) -> None:
        class Failing(ScriptedWorker):
            def run_stage(self, stage_name: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
                if stage_name == "gap_fixing":
                    raise RuntimeError("endpoint unreachable")
                return super().run_stage(stage_name, payload)

        worker = Failing({"gap_finding": GAP_FINDING_OK})
        result = run_artifact_lifecycle(trigger(), worker, state_root=tmp_path)
        assert result["status"] == "error"
        assert result["stage"] == "gap_fixing"
        assert "endpoint unreachable" in result["detail"]

    def test_worker_timeout_returns_bounded_error(self, tmp_path: Path) -> None:
        class Hanging:
            def run_stage(self, stage_name: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
                time.sleep(5.0)
                return GAP_FINDING_OK

        started = time.monotonic()
        result = run_artifact_lifecycle(
            trigger(), Hanging(), stage_timeout_seconds=0.2, state_root=tmp_path
        )
        elapsed = time.monotonic() - started
        assert result["status"] == "error"
        assert result["stage"] == "gap_finding"
        assert "timed out" in result["detail"]
        assert elapsed < 3.0  # bounded well under the worker's hang

    def test_malformed_worker_output_is_error_not_transition(self, tmp_path: Path) -> None:
        worker = ScriptedWorker(
            {
                "gap_finding": GAP_FINDING_OK,
                "gap_fixing": {"unexpected": "shape"},
            }
        )
        result = run_artifact_lifecycle(trigger(), worker, state_root=tmp_path)
        assert result["status"] == "error"
        assert result["stage"] == "gap_fixing"
        assert "stage schema" in result["detail"]
        # No partial result persisted as improved
        persisted = read_last_result("artifact.test", state_root=tmp_path)
        assert persisted is not None and persisted["status"] == "error"


class TestEvidenceBasis:
    def test_evidence_basis_present_when_supplied(self, tmp_path: Path) -> None:
        worker = happy_worker()
        worker.outputs["gap_finding"] = {
            "gaps": [
                {"gap": "no CI step", "evidence_basis": "ci.yml runs uv run pytest"},
            ]
        }
        result = run_artifact_lifecycle(trigger(), worker, state_root=tmp_path)
        assert result["gap_findings"][0]["evidence_basis"] == "ci.yml runs uv run pytest"

    def test_evidence_basis_absent_when_not_supplied(self, tmp_path: Path) -> None:
        worker = happy_worker()
        result = run_artifact_lifecycle(trigger(), worker, state_root=tmp_path)
        for finding in result["gap_findings"]:
            assert "evidence_basis" not in finding
        for fix in result["gap_fixes"]:
            assert "evidence_basis" not in fix


class TestLocalStageWorkerParsing:
    def test_fenced_json_answer_is_parsed(self) -> None:
        class FakeLocal:
            structured_output = True

            def execute(self, task):  # noqa: ANN001 - matches LocalWorker protocol
                class Output:
                    answer = '```json\n{"gaps": []}\n```'

                return Output()

        worker = LocalStageWorker(worker=FakeLocal())
        assert worker.run_stage("gap_finding", {"artifact": "x"}) == {"gaps": []}

    def test_non_json_answer_raises_stage_output_error(self) -> None:
        from audisor.audisor_lifecycle.artifact_flow import StageOutputError

        class FakeLocal:
            structured_output = True

            def execute(self, task):  # noqa: ANN001
                class Output:
                    answer = "I could not produce JSON, sorry."

                return Output()

        worker = LocalStageWorker(worker=FakeLocal())
        with pytest.raises(StageOutputError):
            worker.run_stage("gap_finding", {"artifact": "x"})

    def test_extra_model_keys_are_pruned_to_stage_schema(self) -> None:
        class FakeLocal:
            structured_output = True

            def execute(self, task):  # noqa: ANN001
                class Output:
                    answer = json.dumps(
                        {
                            "passed": True,
                            "summary": "ok",
                            "findings": [{"finding": "minor", "description": "chatter"}],
                            "confidence": 0.9,
                        }
                    )

                return Output()

        worker = LocalStageWorker(worker=FakeLocal())
        # The adapter no longer repairs; it returns the parsed output as
        # emitted (minus empty optionals) — the engine owns recorded repair.
        result = worker.run_stage("evaluation", {"artifact": "x"})
        assert result["confidence"] == 0.9
        assert result["findings"][0]["description"] == "chatter"
