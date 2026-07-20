from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from audisor.audisor_lifecycle.analysis_package import validate_analysis_request


def _fixture_root() -> Path:
    root = Path(__file__).resolve().parents[4] / "aflow" / "tests" / "fixtures" / "05-fully-proven" / "input"
    if not (root / "plan.json").is_file():
        raise ValueError("frozen_analysis_fixture_unavailable")
    return root


def build_analysis_request(*, operation_id: str, prepared: Any) -> dict[str, Any]:
    """Bind the exact frozen request shape to the persisted prepared Build."""
    root = _fixture_root()
    read = lambda name: json.loads((root / name).read_text(encoding="utf-8"))
    context = prepared.instruction.execution_context
    if context is None:
        raise ValueError("prepared_build_contract_incomplete")
    request = {
        "schema_version": "1.0.0",
        "analysis_id": operation_id,
        "success_definition": read("success-definition.json"),
        "plan": read("plan.json"),
        "authority_evidence": read("authority-evidence.json"),
        "repository_evidence": read("repository-evidence.json"),
        "baseline": read("baseline.json"),
        "evidence": read("evidence.json"),
    }
    plan = request["plan"]
    plan["plan_id"] = f"plan.{prepared.instruction.build_id}"
    plan["success_definition_reference"]["artifact_id"] = f"success.{prepared.instruction.build_id}"
    tasks = getattr(prepared.plan, "tasks", ())
    outputs = sorted({path for task in tasks for path in task.expected_outputs}) or ["src/output.txt"]
    plan["target_paths"] = [{"authority_status": "allowed", "path": path, "reason": "Prepared Build output"} for path in outputs]
    plan["actions"][0]["target_paths"] = outputs
    plan["actions"][0]["expected_outputs"] = [
        {"artifact_path": path, "description": "Prepared Build output.", "output_id": f"output.{index}"}
        for index, path in enumerate(outputs, start=1)
    ]
    plan["validations"][0]["action_ids"] = [plan["actions"][0]["action_id"]]
    request["success_definition"]["success_definition_id"] = f"success.{prepared.instruction.build_id}"
    for section in ("authority_evidence", "repository_evidence", "baseline"):
        request[section]["repository_root"] = context.repository_identity["root_reference"]
    request["repository_evidence"]["state"] = {
        "dirty_state": context.repository_identity["dirty_state"],
        "git_head": context.repository_identity["revision"],
        "git_state": "unborn" if context.repository_identity["revision"] == "unborn" else "committed",
    }
    request["baseline"]["dirty_state"] = context.repository_identity["dirty_state"]
    request["baseline"]["git_head"] = context.repository_identity["revision"]
    request["baseline"]["scope"]["relevant_paths"] = list(context.allowed_write_paths)
    request["baseline"]["entries"] = [
        {"classification": "relevant", "content_hash": "sha256:" + "0" * 64, "path": path}
        for path in outputs
    ]
    return validate_analysis_request(copy.deepcopy(request))
