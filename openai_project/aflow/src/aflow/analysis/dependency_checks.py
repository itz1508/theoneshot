from __future__ import annotations

from typing import Any

from .evidence_checks import make_finding, request_evidence_ref


def check(request: dict[str, Any]) -> list[dict[str, Any]]:
    plan = request["plan"]
    actions = {item["action_id"]: item for item in plan["actions"]}
    evidence = [request_evidence_ref(request)]
    findings: list[dict[str, Any]] = []
    for index, action in enumerate(plan["actions"]):
        for dependency in action["depends_on"]:
            if dependency not in actions:
                findings.append(make_finding(
                    f"dependency.missing.{action['action_id']}", "dependency_gap", "deterministic",
                    [f"/plan/actions/{index}/depends_on"], f"Action {action['action_id']} depends on unknown action {dependency}.", evidence,
                    requirement_references=action["requirement_ids"],
                ))

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(action_id: str, chain: list[str]) -> None:
        if action_id in visiting:
            cycle = chain[chain.index(action_id):] + [action_id]
            findings.append(make_finding(
                f"dependency.cycle.{len(findings)+1}", "dependency_gap", "deterministic",
                ["/plan/actions"], f"Action dependency cycle detected: {' -> '.join(cycle)}.", evidence,
            ))
            return
        if action_id in visited or action_id not in actions:
            return
        visiting.add(action_id)
        for dependency in actions[action_id]["depends_on"]:
            visit(dependency, chain + [dependency])
        visiting.remove(action_id)
        visited.add(action_id)

    for action_id in actions:
        visit(action_id, [action_id])
    for index, dependency in enumerate(plan["dependencies"]):
        if dependency["status"] == "unresolved":
            findings.append(make_finding(
                f"dependency.unresolved.{dependency['dependency_id']}", "dependency_gap", "deterministic",
                [f"/plan/dependencies/{index}/status"], f"Dependency {dependency['dependency_id']} is unresolved.", evidence,
            ))
    return findings

