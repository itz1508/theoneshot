"""Fulfilment coordinator closure proof.

Proves the reconciliation criterion: analyze() findings are classified and
routed through FulfilmentCoordinator participants, the corrected plan carries
zero remaining findings, and close_findings() accepts the revision with the
new decision execution-ready.
"""

from copy import deepcopy

from aflow.analysis.decision_engine import analyze
from aflow.fixtures.factory import analysis_evidence, evidence, fixed_clock
from aflow.fulfilment.coordinator import (
    EvidenceRequestParticipant,
    FulfilmentCoordinator,
    PlanRevisionParticipant,
)
from aflow.fulfilment.registry import ParticipantRegistry
from aflow.lifecycle.closure import close_findings
from aflow.storage.hashing import artifact_ref


GAP_TYPE_TO_CLASS = {
    "output_environment_gap": "evidence_gap",
    "validation_evidence_gap": "plan_structure_defect",
}


def _defective_request(clean_request):
    defective = deepcopy(clean_request)
    validation = defective["plan"]["validations"][0]
    validation["required_environment"] = "same-process-test"
    validation["evidence_expected"] = ["artifact"]
    defective["evidence"] = analysis_evidence(
        environment_claim="The visible test proves persistence only within the same process; restart recovery is not exercised."
    )
    return defective


def _coordinator_findings(decision):
    return [
        {
            "id": finding["finding_id"],
            "class": GAP_TYPE_TO_CLASS[finding["gap_type"]],
            "reason": finding["specific_claim"],
            "correction": finding["required_closure"]["description"],
        }
        for finding in decision["findings"]
    ]


def test_fulfilment_coordinator_closes_analysis_findings(clean_request):
    defective = _defective_request(clean_request)
    prior = analyze(defective, clock=fixed_clock)
    assert prior["execution_ready"] is False
    assert [item["gap_type"] for item in prior["findings"]] == [
        "output_environment_gap",
        "validation_evidence_gap",
    ]

    registry = ParticipantRegistry()
    registry.register("evidence_gap", EvidenceRequestParticipant())
    registry.register("plan_structure_defect", PlanRevisionParticipant())
    coordinator = FulfilmentCoordinator(registry)

    findings = _coordinator_findings(prior)
    assert [coordinator.classify(item) for item in findings] == [
        "evidence_required",
        "plan_revision_required",
    ]

    summary = coordinator.fulfil(findings, defective["plan"])
    assert summary.total_findings == 2
    assert [result.status for result in summary.results] == ["needs_external_tool", "fulfilled"]
    assert summary.needs_human_decision == 0
    assert summary.unfulfillable == 0
    assert summary.execution_ready is True

    revised = deepcopy(defective)
    revised["analysis_id"] = "analysis.fulfilment-revised"
    revised["plan"]["version"] = "1.0.1"
    revised["plan"]["validations"][0]["required_environment"] = "production-host"
    revised["plan"]["validations"][0]["evidence_expected"] = ["artifact", "test_result"]
    added = evidence(
        "evidence.fulfilment-closure", "artifact",
        "Production-host evidence supplied for the corrected validation.",
        visibility="closure_input",
    )
    revised["evidence"].append(added)

    closure_request = {
        "schema_version": "1.0.0", "closure_id": "closure.fulfilment",
        "prior_decision_reference": artifact_ref(prior, "analysis-decision.schema.json", id_field="analysis_id"),
        "original_plan_reference": artifact_ref(defective["plan"], "plan.schema.json", id_field="plan_id"),
        "revised_plan": revised["plan"], "added_evidence": [added],
    }
    closure = close_findings(
        closure_request, prior_decision=prior, original_plan=defective["plan"],
        revised_analysis_request=revised, clock=fixed_clock,
    )
    assert [item["status"] for item in closure["finding_results"]] == ["closed", "closed"]
    assert closure["new_decision"]["decision"] == "no_material_gap"
    assert closure["new_decision"]["execution_ready"] is True
    assert closure["new_decision"]["findings"] == []


def test_unroutable_finding_blocks_execution_readiness():
    coordinator = FulfilmentCoordinator(ParticipantRegistry())
    summary = coordinator.fulfil(
        [{"id": "finding.mystery", "class": "unclassified_gap"}], {}
    )
    assert coordinator.classify({"id": "finding.mystery", "class": "unclassified_gap"}) == "unknown"
    assert summary.unfulfillable == 1
    assert summary.execution_ready is False
