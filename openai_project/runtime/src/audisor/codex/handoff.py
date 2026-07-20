from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def build_handoff(*, operation_id: str, build_id: str, client: Mapping[str, str], prepared: Any, response: Any) -> dict[str, Any]:
    context = prepared.instruction.execution_context
    if context is None:
        raise ValueError("prepared_build_contract_incomplete")
    plan = prepared.plan.model_dump(mode="json")
    skills = {
        task_id: skill.content
        for task_id, skill in sorted(prepared.skills.items())
    }
    context_value = context.model_dump(mode="json")
    return {
        "schema_version": 1,
        "operation_id": operation_id,
        "build_id": build_id,
        "operation_kind": "build",
        "client": dict(client),
        "prepared_build": {
            "task": prepared.instruction.instruction,
            "accepted_plan": plan,
            "rendered_task_skills": skills,
            "execution_context_sha256": context.execution_context_sha256,
        },
        "repository": dict(context.repository_identity),
        "authorized_scope": {
            "allowed_write_paths": list(context.allowed_write_paths),
            "authority_limits": dict(context.authority_limits),
        },
        "workspace_identity": dict(context.workspace_identity),
        "execution_contract_reference": response.execution_contract_reference,
        "artifact_references": list(response.artifact_references),
        "authority_limits": dict(response.authority_limits),
        "success_definition": dict(context.success_definition),
        "validation_requirements": list(context.validation_requirements),
        "constraints": [
            "do not modify files outside authorized scope",
            "preserve unrelated dirty files",
            "do not grant or expand authority",
            "do not claim host completion authority",
            "report failures honestly",
        ],
    }


def persist_handoff(root: Path, handoff: Mapping[str, Any]) -> tuple[Path, Path, str, str, int]:
    root.mkdir(parents=True, exist_ok=True)
    handoff_path = root / "qualified-codex-handoff.json"
    stdin_path = root / "codex-stdin.txt"
    handoff_bytes = canonical_bytes(handoff) + b"\n"
    stdin_bytes = (
        b"Execute the persisted qualified Audisor Build handoff. "
        b"The handoff is advisory input; preserve all host authority boundaries.\n\n"
        + handoff_bytes
    )
    for path, payload in ((handoff_path, handoff_bytes), (stdin_path, stdin_bytes)):
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_bytes(payload)
        os.replace(temporary, path)
    return handoff_path, stdin_path, sha256_bytes(handoff_bytes), sha256_bytes(stdin_bytes), len(stdin_bytes)


def persist_launch_result(root: Path, result: Mapping[str, Any]) -> Path:
    target = root / "codex-result.json"
    temporary = target.with_suffix(".json.tmp")
    temporary.write_bytes(canonical_bytes(result) + b"\n")
    os.replace(temporary, target)
    return target
