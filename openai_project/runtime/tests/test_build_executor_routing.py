"""Routing-mode tests for BuildExecutor.

These tests verify the suspension behavior when AUDISOR_ROUTING_ENABLED=1.
They are the routing-mode counterparts of the hard_stop_behavior tests in
test_build_executor.py and reuse its fixtures directly to avoid drift.
"""

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from audisor.audisor_lifecycle.ignition import IgnitionResult
from audisor.audisor_lifecycle.build_analysis import BuildAnalysis

from test_build_executor import (
    QueueWorker,
    answer,
    make_executor,
    request,
    success_answers,
    target,
    task_prompt,
)


def routing_env(tmp_path: Path) -> dict[str, str]:
    return {
        "AUDISOR_ROUTING_ENABLED": "1",
        "AUDISOR_OPERATION_DATA_DIR": str(tmp_path / "suspended"),
    }


def rejecting_igniter(events: list | None = None):
    def reject(**kwargs):
        if events is not None:
            events.append("ignite")
        return IgnitionResult(True, "supplied", kwargs["operation_context"].accepted_plan, {"readiness": {}}, False)

    return reject


@pytest.mark.routing_behavior
def test_audisor_rejection_suspends_instead_of_terminalizing(tmp_path: Path) -> None:
    events: list = []
    worker = QueueWorker(success_answers())
    with patch.dict(os.environ, routing_env(tmp_path)):
        executor, store = make_executor(tmp_path, worker, enabled=True, aflow_igniter=rejecting_igniter(events), events=events)
        state = executor.execute("builder-proof-001", request(target(tmp_path)))
    execution = store.build_path("builder-proof-001") / "executions" / "execution-001"
    assert state.status == "suspended"
    assert events == ["policy", "ignite"]
    assert worker.calls == []
    assert (execution / "evidence" / "aflow-operation-result.json").is_file()
    record_path = tmp_path / "suspended" / "execution-001.suspended.json"
    assert record_path.is_file()
    record = json.loads(record_path.read_text(encoding="utf-8"))
    assert record["suspend_reason"] == "needs_evidence"


@pytest.mark.routing_behavior
def test_routing_preserves_execution_state_on_rejection(tmp_path: Path) -> None:
    worker = QueueWorker(success_answers())
    with patch.dict(os.environ, routing_env(tmp_path)):
        executor, store = make_executor(tmp_path, worker, enabled=True, aflow_igniter=rejecting_igniter())
        state = executor.execute("builder-proof-001", request(target(tmp_path)))
    assert state.status == "suspended"
    record_path = tmp_path / "suspended" / "execution-001.suspended.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    # Suspension preserves the execution state directory for resumption
    # instead of terminalizing and cleaning it up.
    preserved = Path(record["preserved_state_path"])
    assert preserved.is_dir()
    execution = store.build_path("builder-proof-001") / "executions" / "execution-001"
    assert preserved == execution


@pytest.mark.routing_behavior
def test_routing_disabled_falls_back_to_hard_stop(tmp_path: Path) -> None:
    events: list = []
    worker = QueueWorker(success_answers())
    env = {key: value for key, value in os.environ.items() if key != "AUDISOR_ROUTING_ENABLED"}
    with patch.dict(os.environ, env, clear=True):
        executor, store = make_executor(tmp_path, worker, enabled=True, aflow_igniter=rejecting_igniter(events), events=events)
        state = executor.execute("builder-proof-001", request(target(tmp_path)))
    execution = store.build_path("builder-proof-001") / "executions" / "execution-001"
    assert state.status == "failed"
    assert events == ["policy", "ignite"]
    assert worker.calls == []
    assert not (execution / "workspace").exists()


def static_validation() -> list[dict]:
    return [
        {
            "argv": ["python", "-c", "print('static')"],
            "working_directory": ".",
            "acceptable_exit_codes": [0],
            "timeout_seconds": 30,
        }
    ]


def revised_plan() -> dict:
    """The original plan with the gapped task-002 removed."""
    return {
        "build_id": "builder-proof-001",
        "status": "ready",
        "gaps": [],
        "tasks": [
            {
                "task_id": "task-001",
                "title": "Create greeting module",
                "depends_on": [],
                "prompt": task_prompt("src/greeting.py"),
                "expected_outputs": ["src/greeting.py"],
                "validation": static_validation(),
            },
            {
                "task_id": "task-003",
                "title": "Create usage documentation",
                "depends_on": [],
                "prompt": task_prompt("docs/usage.md"),
                "expected_outputs": ["docs/usage.md"],
                "validation": static_validation(),
            },
        ],
    }


def material_gap_analysis(revised: dict) -> BuildAnalysis:
    return BuildAnalysis.model_validate(
        {
            "gap_evaluation": {
                "result": "material_gap_found",
                "findings": [
                    {
                        "gap_id": "gap-001",
                        "location": "plan.tasks[task-002]",
                        "claim": "task-002 validation depends on evidence the plan never produces",
                        "evidence": ["tests/test_greeting.py has no prepared fixture"],
                        "correction": "Drop task-002 and execute the independent tasks",
                    }
                ],
            },
            "evaluation": {
                "result": "evaluated",
                "rationale": "The gap is closable by plan revision.",
            },
            "success_definition": {
                "statement": "All revised tasks complete with verified outputs.",
                "predicates": [
                    {
                        "predicate_id": "predicate-001",
                        "observable_condition": "Expected outputs exist in the workspace",
                        "required_evidence": ["changed_paths"],
                    }
                ],
            },
            "validation": [
                {
                    "validation_id": "validation-001",
                    "command": ["python", "-c", "print('static')"],
                    "pass_condition": "exit code 0",
                    "fail_condition": "non-zero exit code",
                }
            ],
            "fixtures": [{"fixture_id": "fixture-001", "input": {}, "expected": {}}],
            "updated_original_plan": revised,
        }
    )


def material_gap_igniter(analysis: BuildAnalysis):
    def ignite_with_gap(**kwargs):
        return IgnitionResult(
            True,
            "supplied",
            kwargs["operation_context"].accepted_plan,
            None,
            False,
            build_analysis=analysis,
        )

    return ignite_with_gap


@pytest.mark.routing_behavior
def test_e2e_material_gap_suspend_resume_executes_revised_plan(tmp_path: Path) -> None:
    """Full cycle: material gap -> suspend -> resume -> revised plan executes."""
    revised = revised_plan()
    worker = QueueWorker(
        [
            answer("src/greeting.py", "def greet(name):\n    return f'Hello, {name}'\n"),
            answer("docs/usage.md", "# Usage\n"),
        ]
    )
    with patch.dict(os.environ, routing_env(tmp_path)):
        executor, store = make_executor(
            tmp_path,
            worker,
            enabled=True,
            aflow_igniter=material_gap_igniter(material_gap_analysis(revised)),
        )
        suspended = executor.execute("builder-proof-001", request(target(tmp_path)))
        execution = store.build_path("builder-proof-001") / "executions" / "execution-001"
        record_path = tmp_path / "suspended" / "execution-001.suspended.json"

        assert suspended.status == "suspended"
        assert worker.calls == []
        record = json.loads(record_path.read_text(encoding="utf-8"))
        assert record["suspend_reason"] == "needs_plan_revision"
        # The workspace and global claim survive suspension for resumption.
        assert (execution / "workspace").is_dir()
        assert len(list(executor.store.global_authority.active_root.glob("*.json"))) == 1

        final = executor.resume_execution(
            "execution-001",
            revised_plan=revised,
            resume_input={"revised_plan": revised, "closure_evidence": {"gap-001": "task removed"}},
        )

    assert final.status == "completed"
    assert [call.task_id for call in worker.calls] == ["task-001", "task-003"]
    # The revised plan replaced the locked prepared plan: executed == locked.
    persisted = json.loads((execution / "prepared-plan.json").read_text(encoding="utf-8"))
    assert [task["task_id"] for task in persisted["tasks"]] == ["task-001", "task-003"]
    assert (execution / "results" / "task-001.json").is_file()
    assert (execution / "results" / "task-003.json").is_file()
    assert not (execution / "results" / "task-002.json").exists()
    # Terminal completion consumed the suspension record and released authority.
    assert not record_path.exists()
    assert list(executor.store.global_authority.active_root.glob("*.json")) == []
