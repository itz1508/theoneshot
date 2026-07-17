from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any

from .evidence_checks import make_finding, request_evidence_ref


def _overlaps(left: str, right: str) -> bool:
    a, b = PurePosixPath(left), PurePosixPath(right)
    return a == b or a in b.parents or b in a.parents


def _within(path: str, allowed_root: str) -> bool:
    child, parent = PurePosixPath(path), PurePosixPath(allowed_root)
    return child == parent or parent in child.parents


def check(request: dict[str, Any]) -> list[dict[str, Any]]:
    plan = request["plan"]
    evidence = [request_evidence_ref(request)]
    findings: list[dict[str, Any]] = []
    targets = plan["target_paths"]
    excluded = plan["excluded_paths"]
    for index, target in enumerate(targets):
        if target["authority_status"] != "allowed":
            findings.append(make_finding(
                f"authority.target.{index}", "authority_ambiguity", "deterministic",
                [f"/plan/target_paths/{index}/authority_status"], f"Target {target['path']} is not explicitly allowed.", evidence,
            ))
        for excluded_index, item in enumerate(excluded):
            if _overlaps(target["path"], item["path"]):
                findings.append(make_finding(
                    f"authority.conflict.{index}.{excluded_index}", "authority_ambiguity", "deterministic",
                    [f"/plan/target_paths/{index}/path", f"/plan/excluded_paths/{excluded_index}/path"],
                    f"Target {target['path']} overlaps excluded path {item['path']}.", evidence,
                ))
    for action_index, action in enumerate(plan["actions"]):
        for path_index, path in enumerate(action["target_paths"]):
            allowed = any(item["authority_status"] == "allowed" and _within(path, item["path"]) for item in targets)
            prohibited = any(_overlaps(path, item["path"]) for item in excluded)
            if not allowed or prohibited:
                findings.append(make_finding(
                    f"authority.action-target.{action_index}.{path_index}", "authority_ambiguity", "deterministic",
                    [f"/plan/actions/{action_index}/target_paths/{path_index}"],
                    f"Action target {path} is outside allowed plan targets or overlaps an excluded path.", evidence,
                    requirement_references=action["requirement_ids"],
                ))
        for output_index, output in enumerate(action["expected_outputs"]):
            path = output.get("artifact_path")
            if path is None:
                continue
            allowed = any(item["authority_status"] == "allowed" and _within(path, item["path"]) for item in targets)
            within_action = any(_within(path, action_path) for action_path in action["target_paths"])
            prohibited = any(_overlaps(path, item["path"]) for item in excluded)
            if not allowed or not within_action or prohibited:
                findings.append(make_finding(
                    f"authority.output-target.{action_index}.{output_index}", "authority_ambiguity", "deterministic",
                    [f"/plan/actions/{action_index}/expected_outputs/{output_index}/artifact_path"],
                    f"Expected output path {path} is outside its action and approved target containment boundaries.", evidence,
                    requirement_references=action["requirement_ids"],
                ))
    for index, risk in enumerate(plan["authority_risks"]):
        if risk["status"] == "unresolved":
            findings.append(make_finding(
                f"authority.risk.{risk['risk_id']}", "authority_ambiguity", "deterministic",
                [f"/plan/authority_risks/{index}/status"], f"Authority risk {risk['risk_id']} remains unresolved.", evidence,
            ))
    return findings
