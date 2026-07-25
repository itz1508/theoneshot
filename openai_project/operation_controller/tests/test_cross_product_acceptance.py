"""Cross-product acceptance flow: Operation Controller + real A-Flow core.

Proves the overall acceptance criterion with no fakes on the review and
fulfilment surfaces:

  1. Raw task submitted -> operation_id assigned -> planner creates candidate
     plan -> A-Flow reviews -> gap found -> fulfilment runs -> closure
     verifies -> re-review clean -> sandbox_ready.
  2. Prepared plan submitted -> skips planning -> direct A-Flow review ->
     same downstream.

Review is the real ``aflow.analysis.decision_engine.analyze`` and fulfilment
is the real ``FulfilmentCoordinator`` plus ``close_findings`` closure
verification; only the adapter translation layer is local to this test.
"""

from __future__ import annotations

import time
from copy import deepcopy
from pathlib import Path
from typing import Any

from aflow.analysis.decision_engine import analyze
from aflow.fixtures.factory import analysis_evidence, evidence, fixed_clock, request_bundle
from aflow.fulfilment.coordinator import FulfilmentCoordinator
from aflow.lifecycle.closure import close_findings
from aflow.storage.hashing import artifact_ref

from operation_controller.controller import FileOperationStore, OperationController
from operation_controller.states import OperationState

BLOCKING_DECISIONS = {"material_gap_found", "missing_evidence", "contradicted", "drift_revalidation_required"}
GAP_TYPE_TO_CLASS = {
    "output_environment_gap": "evidence_gap",
    "validation_evidence_gap": "plan_structure_defect",
}


def defective_request() -> dict[str, Any]:
    request = request_bundle()
    validation = request["plan"]["validations"][0]
    validation["required_environment"] = "same-process-test"
    validation["evidence_expected"] = ["artifact"]
    request["evidence"] = analysis_evidence(
        environment_claim="The visible test proves persistence only within the same process; restart recovery is not exercised."
    )
    return request


class AflowReviewAdapter:
    """Real A-Flow analysis behind the controller's ReviewAdapter protocol."""

    def __init__(self) -> None:
        self.decisions: list[dict[str, Any]] = []

    def review(self, plan: dict[str, Any], operation_id: str) -> dict[str, Any]:
        request = deepcopy(plan)
        request["analysis_id"] = f"analysis.{operation_id}.{len(self.decisions)}"
        decision = analyze(request, clock=fixed_clock)
        self.decisions.append(decision)
        if decision["decision"] == "no_material_gap":
            return {"decision": "no_material_gap", "findings": []}
        if decision["decision"] in BLOCKING_DECISIONS:
            findings = [
                {
                    "id": finding["finding_id"],
                    "class": GAP_TYPE_TO_CLASS.get(finding["gap_type"], "evidence_gap"),
                    "reason": finding["specific_claim"],
                    "correction": finding["required_closure"]["description"],
                    "aflow_decision": decision,
                }
                for finding in decision["findings"]
            ]
            return {"decision": "material_gap_found", "findings": findings}
        return {"decision": decision["decision"], "findings": decision["findings"]}


class AflowFulfilmentAdapter:
    """Real fulfilment coordinator + closure verification."""

    def __init__(self) -> None:
        self.coordinator = FulfilmentCoordinator()
        self.summaries: list[Any] = []
        self.closures: list[dict[str, Any]] = []

    def fulfil(self, findings: list[dict[str, Any]], plan: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        summary = self.coordinator.fulfil(findings, plan)
        self.summaries.append(summary)
        if not summary.execution_ready:
            return {"all_resolved": False, "unresolved": findings}

        prior = findings[0]["aflow_decision"]
        original = deepcopy(plan)
        revised = deepcopy(plan)
        revised["analysis_id"] = f"{prior['analysis_id']}.revised"
        revised["plan"]["version"] = "1.0.1"
        revised["plan"]["validations"][0]["required_environment"] = "production-host"
        revised["plan"]["validations"][0]["evidence_expected"] = ["artifact", "test_result"]
        added = evidence(
            "evidence.xprod-closure", "artifact",
            "Production-host evidence supplied for the corrected validation.",
            visibility="closure_input",
        )
        revised["evidence"].append(added)
        closure = close_findings(
            {
                "schema_version": "1.0.0", "closure_id": "closure.xprod",
                "prior_decision_reference": artifact_ref(prior, "analysis-decision.schema.json", id_field="analysis_id"),
                "original_plan_reference": artifact_ref(original["plan"], "plan.schema.json", id_field="plan_id"),
                "revised_plan": revised["plan"], "added_evidence": [added],
            },
            prior_decision=prior, original_plan=original["plan"],
            revised_analysis_request=revised, clock=fixed_clock,
        )
        self.closures.append(closure)
        if any(item["status"] != "closed" for item in closure["finding_results"]):
            return {"all_resolved": False, "unresolved": findings}
        return {"all_resolved": True, "revised_plan": revised}


class RequestBundlePlanner:
    """Deterministic planner producing an analysis request from a raw prompt."""

    def __init__(self, request: dict[str, Any]):
        self.calls: list[str] = []
        self._request = request

    def create_plan(self, prompt: str, context: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(prompt)
        return deepcopy(self._request)


def build_controller(tmp_path: Path, planner: RequestBundlePlanner | None = None):
    review = AflowReviewAdapter()
    fulfilment = AflowFulfilmentAdapter()
    store = FileOperationStore(tmp_path / "ops")
    controller = OperationController(
        store=store,
        planning_adapter=planner,
        review_adapter=review,
        fulfilment_adapter=fulfilment,
    )
    return controller, store, review, fulfilment


def test_raw_task_flows_through_gap_fulfilment_closure_to_sandbox(tmp_path: Path) -> None:
    planner = RequestBundlePlanner(defective_request())
    controller, store, review, fulfilment = build_controller(tmp_path, planner)

    started = time.monotonic()
    result = controller.accept(source_kind="task", prompt="produce the durable output")
    elapsed = time.monotonic() - started

    assert result.state == OperationState.SANDBOX_READY
    assert result.detail == {"review_decision": "no_material_gap"}
    assert elapsed < 30

    # Planner produced the candidate plan from the raw prompt
    assert planner.calls == ["produce the durable output"]

    # First review found the material gap; second review of the revised plan is clean
    assert [decision["decision"] for decision in review.decisions] == ["missing_evidence", "no_material_gap"]
    assert review.decisions[1]["execution_ready"] is True
    assert review.decisions[1]["findings"] == []

    # Real fulfilment resolved both findings and closure verified the revision
    summary = fulfilment.summaries[0]
    assert summary.total_findings == 2
    assert summary.execution_ready is True
    assert [item["status"] for item in fulfilment.closures[0]["finding_results"]] == ["closed", "closed"]

    # Persisted lifecycle history covers the full acceptance flow
    record = store.load(result.operation_id)
    states = [entry["to"] for entry in record.history]
    assert states == ["planning", "plan_ready", "reviewing", "fulfilling", "reviewing", "sandbox_ready"]


def test_prepared_plan_skips_planning_and_reaches_sandbox(tmp_path: Path) -> None:
    controller, store, review, fulfilment = build_controller(tmp_path)

    result = controller.accept(source_kind="prepared_plan", plan=defective_request())

    assert result.state == OperationState.SANDBOX_READY
    assert [decision["decision"] for decision in review.decisions] == ["missing_evidence", "no_material_gap"]
    assert fulfilment.summaries[0].execution_ready is True

    record = store.load(result.operation_id)
    states = [entry["to"] for entry in record.history]
    assert states == ["reviewing", "fulfilling", "reviewing", "sandbox_ready"]


def test_clean_prepared_plan_goes_directly_to_sandbox(tmp_path: Path) -> None:
    controller, store, review, fulfilment = build_controller(tmp_path)

    result = controller.accept(source_kind="prepared_plan", plan=request_bundle())

    assert result.state == OperationState.SANDBOX_READY
    assert [decision["decision"] for decision in review.decisions] == ["no_material_gap"]
    assert fulfilment.summaries == []

    record = store.load(result.operation_id)
    states = [entry["to"] for entry in record.history]
    assert states == ["reviewing", "sandbox_ready"]
