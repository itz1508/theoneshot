from __future__ import annotations

from typing import Any

from .evidence_checks import make_finding, request_evidence_ref


def check(request: dict[str, Any]) -> list[dict[str, Any]]:
    plan = request["plan"]
    capabilities = {item["capability_id"]: item for item in request["authority_evidence"]["capabilities"]}
    authority_ids = {item["authority_id"] for item in request["authority_evidence"]["sources"]}
    evidence = [request_evidence_ref(request)]
    findings: list[dict[str, Any]] = []
    for index, action in enumerate(plan["actions"]):
        capability = capabilities.get(action["capability_id"])
        problems: list[str] = []
        if capability is None:
            problems.append("capability is absent from the authority bundle")
        else:
            if not capability["available"]:
                problems.append("capability is unavailable")
            owner = "deterministic_local" if capability["owner"] == "aflow" else capability["owner"]
            if owner != action["owner"]:
                problems.append(f"capability owner is {capability['owner']}, not {action['owner']}")
            activation = capability["activation"]
            if activation["activation_type"] not in {"automatic", "not_required"} and not activation.get("source_authority_id"):
                problems.append("capability activation has no source authority")
            if activation.get("source_authority_id") and activation["source_authority_id"] not in authority_ids:
                problems.append(f"activation source authority {activation['source_authority_id']} does not resolve")
            if activation.get("required_phase") and activation["required_phase"] != action["phase"]:
                problems.append(f"required phase is {activation['required_phase']}, not {action['phase']}")
            deferred_phase_entry = (
                activation["activation_type"] == "phase_entry"
                and activation.get("required_phase") == action["phase"]
                and action["activation"]["activation_type"] == "phase_entry"
                and bool(action["activation"].get("activation_source"))
            )
            if not activation["currently_authorized"] and activation["activation_type"] not in {"automatic", "not_required"} and not deferred_phase_entry:
                problems.append("capability is not currently authorized and has no compatible phase-entry activation")
        deferred_action = (
            action["activation"]["activation_type"] == "phase_entry"
            and bool(action["activation"].get("activation_source"))
            and capability is not None
            and capability["activation"].get("required_phase") == action["phase"]
        )
        if action["activation"].get("activation_source") and action["activation"]["activation_source"] not in authority_ids:
            problems.append(f"action activation source {action['activation']['activation_source']} does not resolve")
        if action["activation"]["activation_type"] != "not_required" and not action["activation"].get("activation_source"):
            problems.append("action activation has no explicit source")
        if not action["activation"]["currently_authorized"] and not deferred_action:
            problems.append("action activation is neither authorized nor an explicit compatible phase-entry deferral")
        unsatisfied = [p["prerequisite_id"] for p in action["prerequisites"] if p["status"] in {"unsatisfied", "unknown"}]
        deferred_prerequisites = [p["prerequisite_id"] for p in action["prerequisites"] if p["status"] == "deferred"]
        if unsatisfied:
            problems.append("unsatisfied prerequisites: " + ", ".join(unsatisfied))
        if deferred_prerequisites and not deferred_action:
            problems.append("deferred prerequisites lack compatible phase-entry activation: " + ", ".join(deferred_prerequisites))
        if problems:
            findings.append(make_finding(
                f"activation.{action['action_id']}", "sequence_activation_gap", "deterministic",
                [f"/plan/actions/{index}"], f"Action {action['action_id']} cannot run in its declared phase: {'; '.join(problems)}.", evidence,
                requirement_references=action["requirement_ids"],
                acceptance_predicates=["The action capability, owner, phase, prerequisites, and activation all agree with authority evidence."],
            ))
    return findings
