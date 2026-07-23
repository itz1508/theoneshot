from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

from audisor.audisor_lifecycle.adapter import assemble_contract
from audisor.audisor_lifecycle.contract import accept_for_primary, frozen_readiness_decision, frozen_tree_digest
from audisor.audisor_lifecycle.hook import evaluate_hook_payload


FIXTURES = Path(__file__).parent / "fixtures" / "aflow_contract"


def source(name: str = "ready-input.json") -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def live_input() -> dict:
    value = source()
    target = "openai_project/runtime/tests/fixtures/aflow_ignition_live/live-proof.txt"
    value["authority"]["allowed_paths"] = ["openai_project/runtime/tests/fixtures/aflow_ignition_live"]
    for action in value["candidate_implementation_plan"]["implementation_plan"]:
        action["target_paths"] = [target]
    return value


def analysis() -> dict:
    return {
        "success_definition": {}, "required_trajectory": {}, "plan_gaps": [], "validation_cases": [], "fixture_specifications": [],
        "lock_payload": {"immutable_user_task_canonical_text": "task\n", "accepted_plan_canonical_text": "plan\n", "success_definition_canonical_text": "success\n", "required_trajectory_canonical_text": "trajectory\n", "validation_cases_canonical_text": "validation\n", "fixture_specifications_canonical_text": "fixtures\n", "hash_algorithm": "sha256"},
        "decision": {"aflow_decision": "no_material_gap", "contract_decision": "no_material_gap", "plan_ready_for_primary_decision": True},
    }


def write_state(root: Path, input_value: dict | None = None, *, drift: str = "valid") -> dict:
    contract = assemble_contract(input_value or live_input())["aflow_execution_contract"]
    lock = accept_for_primary(analysis(), execution_contract_sha256=contract["lock_payload"]["sha256"])
    root.mkdir(parents=True, exist_ok=True)
    (root / "active-lock.json").write_text(json.dumps({"primary_lock": lock, "execution_contract": contract, "drift_state": drift}), encoding="utf-8")
    return contract


def mutation(path: str) -> dict:
    return {"hook_event_name": "PreToolUse", "tool_name": "apply_patch", "requested_targets": [path]}


def audit(result: dict) -> dict:
    return json.loads(Path(result["audit_path"]).read_text(encoding="utf-8"))


def test_01_codex_hook_configuration_is_runtime_authority() -> None:
    root = Path(__file__).resolve().parents[3]
    assert (root / ".codex" / "hooks.json").exists()
    runtime = root / "openai_project" / "runtime" / "src" / "audisor"
    assert not any("hooks.json" in path.read_text(encoding="utf-8", errors="ignore") for path in runtime.rglob("*.py"))


def test_02_every_direct_hook_invocation_writes_an_audit_record(tmp_path: Path) -> None:
    result = evaluate_hook_payload(mutation("openai_project/runtime/tests/fixtures/aflow_ignition_live/live-proof.txt"), tmp_path)
    record = audit(result)
    assert set(record) == {"event", "timestamp", "hook_name", "mutation_tool", "requested_targets", "lock_present", "lock_valid", "authority_valid", "decision", "reason", "exit_code"}


def test_03_missing_lock_denies_an_intercepted_mutation(tmp_path: Path) -> None:
    result = evaluate_hook_payload(mutation("openai_project/runtime/tests/fixtures/aflow_ignition_live/live-proof.txt"), tmp_path)
    assert result["decision"] == "deny" and result["exit_code"] == 1 and audit(result)["lock_present"] is False


def test_04_missing_lock_has_no_silent_allow(tmp_path: Path) -> None:
    assert evaluate_hook_payload(mutation("openai_project/runtime/tests/fixtures/aflow_ignition_live/live-proof.txt"), tmp_path)["decision"] != "allow"


def test_05_tampered_lock_denies(tmp_path: Path) -> None:
    write_state(tmp_path); state = json.loads((tmp_path / "active-lock.json").read_text()); state["primary_lock"]["lock_hash"] = "0" * 64; (tmp_path / "active-lock.json").write_text(json.dumps(state), encoding="utf-8")
    assert evaluate_hook_payload(mutation("openai_project/runtime/tests/fixtures/aflow_ignition_live/live-proof.txt"), tmp_path)["decision"] == "deny"


def test_05b_unbound_legacy_lock_denies(tmp_path: Path) -> None:
    write_state(tmp_path); state = json.loads((tmp_path / "active-lock.json").read_text()); state["primary_lock"]["canonical_payload"].pop("execution_contract_sha256"); (tmp_path / "active-lock.json").write_text(json.dumps(state), encoding="utf-8")
    assert evaluate_hook_payload(mutation("openai_project/runtime/tests/fixtures/aflow_ignition_live/live-proof.txt"), tmp_path)["decision"] == "deny"


def test_06_invalid_contract_denies(tmp_path: Path) -> None:
    write_state(tmp_path); state = json.loads((tmp_path / "active-lock.json").read_text()); state["execution_contract"]["lock_payload"]["canonical_text"] += "x"; (tmp_path / "active-lock.json").write_text(json.dumps(state), encoding="utf-8")
    assert evaluate_hook_payload(mutation("openai_project/runtime/tests/fixtures/aflow_ignition_live/live-proof.txt"), tmp_path)["decision"] == "deny"


def test_07_nonready_contract_denies(tmp_path: Path) -> None:
    value = source("nonready-input.json"); write_state(tmp_path, value)
    assert evaluate_hook_payload(mutation("openai_project/runtime/tests/fixtures/aflow_ignition_live/live-proof.txt"), tmp_path)["decision"] == "deny"


def test_08_valid_lock_and_exact_authorized_target_allows(tmp_path: Path) -> None:
    contract = write_state(tmp_path); target = contract["implementation_plan"][0]["target_paths"][0]
    assert evaluate_hook_payload(mutation(target), tmp_path)["decision"] == "allow"


def test_09_unplanned_sibling_denies(tmp_path: Path) -> None:
    write_state(tmp_path)
    assert evaluate_hook_payload(mutation("openai_project/runtime/tests/fixtures/aflow_ignition_live/other.txt"), tmp_path)["decision"] == "deny"


def test_10_prohibited_child_and_parent_are_denied_directionally(tmp_path: Path) -> None:
    write_state(tmp_path)
    assert evaluate_hook_payload(mutation("openai_project/aflow/file.py"), tmp_path)["decision"] == "deny"
    assert evaluate_hook_payload(mutation("openai_project"), tmp_path)["decision"] == "deny"


def test_11_hook_reuses_existing_verifiers() -> None:
    import audisor.audisor_lifecycle.adapter as adapter
    import audisor.audisor_lifecycle.contract as contract
    import audisor.audisor_lifecycle.hook as hook
    assert hook.verify_contract is adapter.verify_contract and hook.verify_lock is contract.verify_lock


def test_12_hook_errors_are_audited_and_deny(tmp_path: Path) -> None:
    tmp_path.mkdir(exist_ok=True); (tmp_path / "active-lock.json").write_text("not json", encoding="utf-8")
    result = evaluate_hook_payload(mutation("openai_project/runtime/tests/fixtures/aflow_ignition_live/live-proof.txt"), tmp_path)
    assert result["decision"] == "error" and result["exit_code"] == 1 and audit(result)["decision"] == "error"


def test_13_read_only_is_recorded_without_a_lock(tmp_path: Path) -> None:
    result = evaluate_hook_payload({"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "git status --short"}}, tmp_path)
    assert result["decision"] == "allow" and audit(result)["reason"] == "read-only operation"


def test_14_frozen_tree_is_byte_stable() -> None:
    frozen = Path(__file__).resolve().parents[2] / "aflow"
    assert frozen_tree_digest(frozen) == "f0e20a4b7d6c4de71f45ff9dce9df1602c74b750fea6c14255d0cce6df069bb9"


def test_15_frozen_tree_digest_orders_by_relative_posix_path(tmp_path: Path) -> None:
    # Nested layout whose native Path ordering differs between Windows ("\") and
    # POSIX ("/") separators. Windows compares path components, so the shorter
    # component "src" ranks before "src2"; POSIX compares bytes, and "/" (0x2F)
    # sorts before "2" (0x32), so "src/main.txt" ranks before "src2/a.txt". A
    # deterministic digest must follow the repository-relative POSIX path order
    # on every platform.
    (tmp_path / "src").mkdir()
    (tmp_path / "src2").mkdir()
    contents = {"src/main.txt": b"one\n", "src2/a.txt": b"two\n"}
    for relative, payload in contents.items():
        (tmp_path / relative).write_bytes(payload)

    ordered = sorted(contents)  # canonical repository-relative POSIX order
    rows = [
        f"{relative}\0{hashlib.sha256(contents[relative]).hexdigest()}"
        for relative in ordered
    ]
    expected = hashlib.sha256(("\n".join(rows) + "\n").encode("utf-8")).hexdigest()

    assert ordered == ["src/main.txt", "src2/a.txt"]
    assert frozen_tree_digest(tmp_path) == expected
