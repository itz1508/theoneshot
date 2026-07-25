"""Per-slice mutation authority proof through the active hook path.

Covers the Task 3.2 acceptance criteria:
  - schema_version=2 with slices {A: locked, B: awaiting_decision}
    -> hook ALLOWS mutation targeting Slice A paths
    -> hook DENIES mutation targeting Slice B paths
    -> hook ALLOWS read-only tools regardless of slice state
  - legacy active-lock.json (no schema_version) -> global check, unchanged
  - schema_version=2 without slices -> global check (non-sliced operation)

The wire tests run ``python -m audisor.audisor_lifecycle.hook`` as a real
subprocess with stdin payloads and AUDISOR_STATE_ROOT, proving the decision
through the same transport Codex uses.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from audisor.audisor_lifecycle.adapter import assemble_contract
from audisor.audisor_lifecycle.contract import accept_for_primary
from audisor.audisor_lifecycle.hook import evaluate_hook_payload

FIXTURES = Path(__file__).parent / "fixtures" / "aflow_contract"
HOOK_MODULE = "audisor.audisor_lifecycle.hook"

SLICE_A_ROOT = "openai_project/runtime/tests/fixtures/aflow_ignition_live"
SLICE_A_TARGET = f"{SLICE_A_ROOT}/live-proof.txt"
SLICE_B_ROOT = "openai_project/aflow"
SLICE_B_TARGET = f"{SLICE_B_ROOT}/file.py"

SLICES = {
    "slice-a": {"status": "locked", "allowed_paths": [SLICE_A_ROOT]},
    "slice-b": {"status": "awaiting_decision", "allowed_paths": [SLICE_B_ROOT]},
}


def live_input() -> dict:
    value = json.loads((FIXTURES / "ready-input.json").read_text(encoding="utf-8"))
    value["authority"]["allowed_paths"] = [SLICE_A_ROOT]
    for action in value["candidate_implementation_plan"]["implementation_plan"]:
        action["target_paths"] = [SLICE_A_TARGET]
    return value


def analysis() -> dict:
    return {
        "success_definition": {}, "required_trajectory": {}, "plan_gaps": [], "validation_cases": [], "fixture_specifications": [],
        "lock_payload": {"immutable_user_task_canonical_text": "task\n", "accepted_plan_canonical_text": "plan\n", "success_definition_canonical_text": "success\n", "required_trajectory_canonical_text": "trajectory\n", "validation_cases_canonical_text": "validation\n", "fixture_specifications_canonical_text": "fixtures\n", "hash_algorithm": "sha256"},
        "decision": {"aflow_decision": "no_material_gap", "contract_decision": "no_material_gap", "plan_ready_for_primary_decision": True},
    }


def write_state(root: Path, *, schema_version: int | None = None, slices: dict | None = None) -> dict:
    contract = assemble_contract(live_input())["aflow_execution_contract"]
    lock = accept_for_primary(analysis(), execution_contract_sha256=contract["lock_payload"]["sha256"])
    state: dict = {"primary_lock": lock, "execution_contract": contract, "drift_state": "valid"}
    if schema_version is not None:
        state["schema_version"] = schema_version
    if slices is not None:
        state["slices"] = slices
    root.mkdir(parents=True, exist_ok=True)
    (root / "active-lock.json").write_text(json.dumps(state), encoding="utf-8")
    return contract


def mutation(path: str) -> dict:
    return {"hook_event_name": "PreToolUse", "tool_name": "apply_patch", "requested_targets": [path]}


def audit(result: dict) -> dict:
    return json.loads(Path(result["audit_path"]).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# In-process slice authority decisions
# ---------------------------------------------------------------------------


def test_locked_slice_target_is_allowed(tmp_path: Path) -> None:
    write_state(tmp_path, schema_version=2, slices=SLICES)
    result = evaluate_hook_payload(mutation(SLICE_A_TARGET), tmp_path)
    record = audit(result)
    assert result["decision"] == "allow"
    assert result["reason"] == "slice_authority_verified"
    assert result["host_permission_decision"] is None
    assert record["decision"] == "allow" and record["authority_valid"] is True


def test_awaiting_decision_slice_target_is_denied(tmp_path: Path) -> None:
    write_state(tmp_path, schema_version=2, slices=SLICES)
    result = evaluate_hook_payload(mutation(SLICE_B_TARGET), tmp_path)
    assert result["decision"] == "deny"
    assert "slice-b" in result["reason"] and "awaiting_decision" in result["reason"]
    assert audit(result)["decision"] == "deny"


def test_target_outside_every_slice_is_denied(tmp_path: Path) -> None:
    write_state(tmp_path, schema_version=2, slices=SLICES)
    result = evaluate_hook_payload(mutation("docs/notes.md"), tmp_path)
    assert result["decision"] == "deny"
    assert "does not belong to any authorized slice" in result["reason"]


def test_read_only_tool_allowed_regardless_of_slice_state(tmp_path: Path) -> None:
    write_state(tmp_path, schema_version=2, slices=SLICES)
    payload = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "git status --short"}}
    result = evaluate_hook_payload(payload, tmp_path)
    assert result["decision"] == "allow" and result["reason"] == "read-only operation"


def test_mixed_targets_denied_when_any_falls_in_blocked_slice(tmp_path: Path) -> None:
    write_state(tmp_path, schema_version=2, slices=SLICES)
    payload = {"hook_event_name": "PreToolUse", "tool_name": "apply_patch", "requested_targets": [SLICE_A_TARGET, SLICE_B_TARGET]}
    assert evaluate_hook_payload(payload, tmp_path)["decision"] == "deny"


# ---------------------------------------------------------------------------
# Migration rule: legacy and non-sliced states keep the global check
# ---------------------------------------------------------------------------


def test_legacy_state_without_schema_version_uses_global_check(tmp_path: Path) -> None:
    write_state(tmp_path)
    allowed = evaluate_hook_payload(mutation(SLICE_A_TARGET), tmp_path)
    assert allowed["decision"] == "allow"
    assert allowed["reason"] == "requested targets are authorized"
    denied = evaluate_hook_payload(mutation(f"{SLICE_A_ROOT}/other.txt"), tmp_path)
    assert denied["decision"] == "deny"


def test_v2_state_without_slices_uses_global_check(tmp_path: Path) -> None:
    write_state(tmp_path, schema_version=2)
    allowed = evaluate_hook_payload(mutation(SLICE_A_TARGET), tmp_path)
    assert allowed["decision"] == "allow"
    assert allowed["reason"] == "requested targets are authorized"
    assert evaluate_hook_payload(mutation("openai_project/aflow/file.py"), tmp_path)["decision"] == "deny"


# ---------------------------------------------------------------------------
# Active hook path: real subprocess transport (Codex wire format)
# ---------------------------------------------------------------------------


def run_hook(stdin_data: str, state_root: Path) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["AUDISOR_STATE_ROOT"] = str(state_root)
    env.pop("AFLOW_STATE_ROOT", None)
    return subprocess.run(
        [sys.executable, "-m", HOOK_MODULE],
        input=stdin_data,
        capture_output=True,
        text=True,
        env=env,
        cwd=str(Path(__file__).resolve().parents[3]),
    )


def patch_payload(target: str) -> str:
    command = f"*** Begin Patch\n*** Update File: {target}\n@@\n-old\n+new\n*** End Patch"
    return json.dumps({"hook_event_name": "PreToolUse", "tool_name": "apply_patch", "tool_input": {"command": command}})


def test_wire_locked_slice_mutation_allows_exit0_no_stdout(tmp_path: Path) -> None:
    write_state(tmp_path, schema_version=2, slices=SLICES)
    result = run_hook(patch_payload(SLICE_A_TARGET), tmp_path)
    assert result.returncode == 0
    assert result.stdout == ""


def test_wire_blocked_slice_mutation_denies_with_structured_reason(tmp_path: Path) -> None:
    write_state(tmp_path, schema_version=2, slices=SLICES)
    result = run_hook(patch_payload(SLICE_B_TARGET), tmp_path)
    assert result.returncode == 0
    output = json.loads(result.stdout)
    hook_out = output["hookSpecificOutput"]
    assert hook_out["hookEventName"] == "PreToolUse"
    assert hook_out["permissionDecision"] == "deny"
    assert "slice-b" in hook_out["permissionDecisionReason"]


def test_wire_read_only_allowed_with_blocked_slice_present(tmp_path: Path) -> None:
    write_state(tmp_path, schema_version=2, slices=SLICES)
    payload = json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "git status --short"}})
    result = run_hook(payload, tmp_path)
    assert result.returncode == 0
    assert result.stdout == ""
