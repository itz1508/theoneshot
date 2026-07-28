"""Shared scaffolding for the A-Flow artifact lifecycle test suite.

The engine, not the worker, owns stage ordering and the unresolved-gap
barrier. The lifecycle tests use these stub workers and canned stage
outputs to prove the code-owned transitions. Every constant here is a
complete, named scenario input — shared to avoid divergence between the
split test modules, never to hide scenario intent.
"""
from __future__ import annotations

from typing import Any, Mapping


def trigger(**overrides: Any) -> dict[str, Any]:
    base = {
        "artifact_id": "artifact.test",
        "artifact_type": "plan",
        "status": "draft_complete",
        "content": "Build a widget with tests and documentation.",
        "intent": "ship the widget",
        "context": "repo has pytest; CI runs uv run pytest",
    }
    base.update(overrides)
    return base


GAP_FINDING_OK = {"gaps": [{"gap": "no rollout step", "category": "missing"}]}
GAP_FIXING_RESOLVED = {
    "fixes": [{"gap": "no rollout step", "fix": "added rollout step"}],
    "unresolved": [],
    "improved_artifact": "Build a widget with tests, documentation, and rollout.",
}
GAP_FIXING_UNRESOLVED = {
    "fixes": [],
    "unresolved": [
        {
            "gap": "deployment target unknown",
            "why_unresolved": "no repository evidence names an environment",
            "required_to_resolve": ["deployment environment decision"],
            "successful_resolution": "artifact names the deployment target",
        }
    ],
    "improved_artifact": "Build a widget with tests and documentation.",
}
EVALUATION_PASSED = {"passed": True, "findings": [], "summary": "coherent and feasible"}
EVALUATION_FAILED = {
    "passed": False,
    "summary": "artifact contradicts repo constraints",
    "findings": [
        {
            "finding": "widget spec contradicts existing API",
            "required_to_resolve": ["align spec with API"],
            "successful_resolution": "spec matches the API contract",
        }
    ],
}
SUCCESS_CRITERIA_OK = {
    "criteria": [{"criterion": "pytest green", "validation": "uv run pytest exit code 0"}]
}
FIXTURE_DESIGN_OK = {
    "fixture_cases": [
        {"name": "happy path", "expectation": "widget builds", "criterion": "pytest green"}
    ]
}


class ScriptedWorker:
    """Stage worker returning canned outputs while recording invocation order.

    A list value for a stage is consumed one item per invocation, enabling
    repair-cycle scripts where the same stage runs twice with different
    outputs.
    """

    def __init__(self, outputs: Mapping[str, Any]):
        self.outputs = dict(outputs)
        self.calls: list[str] = []
        self.payloads: dict[str, Mapping[str, Any]] = {}
        self.history: list[tuple[str, Mapping[str, Any]]] = []

    def run_stage(self, stage_name: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        self.calls.append(stage_name)
        self.payloads[stage_name] = payload
        self.history.append((stage_name, payload))
        value = self.outputs[stage_name]
        if isinstance(value, list):
            return value.pop(0)
        return value


def happy_worker() -> ScriptedWorker:
    return ScriptedWorker(
        {
            "gap_finding": GAP_FINDING_OK,
            "gap_fixing": GAP_FIXING_RESOLVED,
            "evaluation": EVALUATION_PASSED,
            "success_criteria": SUCCESS_CRITERIA_OK,
            "fixture_design": FIXTURE_DESIGN_OK,
        }
    )
