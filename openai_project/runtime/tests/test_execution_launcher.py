"""Deterministic evidence for the external locked-contract launcher."""
from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from audisor.audisor_lifecycle.adapter import assemble_contract
from audisor.audisor_lifecycle.contract import accept_for_primary, frozen_tree_digest, write_lock
from audisor.execution_launcher import _changes, launch_execution


FROZEN = Path(__file__).resolve().parents[2] / "aflow"
SOURCE = Path(__file__).parent / "fixtures" / "aflow_contract" / "ready-input.json"


def digest(value: bytes) -> str: return hashlib.sha256(value).hexdigest()


def entries(root: Path) -> list[dict]:
    result = []
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        rel = path.relative_to(root).as_posix()
        if rel == ".git" or rel.startswith(".git/"):
            continue
        if path.is_dir(): result.append({"path": rel, "kind": "directory", "mode": stat.S_IMODE(path.stat().st_mode)})
        elif path.is_symlink(): result.append({"path": rel, "kind": "symlink", "mode": stat.S_IMODE(path.lstat().st_mode), "target": os.readlink(path)})
        else:
            data = path.read_bytes(); result.append({"path": rel, "kind": "file", "mode": stat.S_IMODE(path.stat().st_mode), "sha256": digest(data), "size": len(data), "binary": b"\0" in data})
    return result


def snapshot(source: Path, path: Path) -> tuple[Path, str]:
    rows = entries(source); tree = digest((json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode())
    path.write_text(json.dumps({"source_root": str(source), "entries": rows, "tree_sha256": tree}, sort_keys=True), encoding="utf-8")
    return path, digest(path.read_bytes())


def prepared(tmp_path: Path, *, command: list[str] | None = None, cleanup: str = "preserve", baseline_type: str = "working_tree_snapshot", include_execution: bool = True, validation_command: list[str] | None = None) -> tuple[dict, Path, Path, Path]:
    repo = tmp_path / "primary"; (repo / "allowed").mkdir(parents=True); (repo / "allowed" / "base.txt").write_text("base\n", encoding="utf-8")
    if baseline_type == "working_tree_snapshot":
        (repo / "untracked.txt").write_text("untracked\n", encoding="utf-8")
        manifest, manifest_sha = snapshot(repo, tmp_path / "accepted-snapshot.json")
        baseline = {"type": "working_tree_snapshot", "identifier": str(manifest), "manifest_sha256": manifest_sha}
    else:
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "launcher@example.test"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Launcher"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "add", "allowed/base.txt"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "baseline"], cwd=repo, check=True, capture_output=True)
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()
        baseline = {"type": "commit", "identifier": commit, "manifest_sha256": digest((json.dumps(entries(repo), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode())}
    value = json.loads(SOURCE.read_text(encoding="utf-8"))
    value["accepted_task_input"].update({"accepted_baseline": baseline, "drift_state": "valid"})
    value["authority"].update({"allowed_paths": ["allowed"], "prohibited_paths": ["forbidden"], "allowed_tools": ["run_command"], "prohibited_tools": []})
    value["candidate_implementation_plan"]["implementation_plan"][0]["target_paths"] = ["allowed"]
    value["candidate_implementation_plan"]["implementation_plan"][1]["target_paths"] = ["allowed"]
    for action in value["candidate_implementation_plan"]["implementation_plan"]: action["authorized_mutation_types"] = ["created", "modified", "deleted", "renamed", "mode", "symlink", "rename"]
    if include_execution:
        validation = value["candidate_implementation_plan"]["validation_contract"][0]
        validation["execution"] = {"command": validation_command or [sys.executable, "-c", "import sys;sys.exit(0)"], "working_directory": ".", "network": False, "timeout_seconds": 10, "evidence_outputs": []}
        value["candidate_implementation_plan"]["validation_contract"][1]["execution"] = validation["execution"]
    contract = assemble_contract(value)["aflow_execution_contract"]
    contract_path = tmp_path / "contract.json"; contract_path.write_text(json.dumps(contract), encoding="utf-8")
    lock_path = tmp_path / "active-lock.json"; write_lock(lock_path, accept_for_primary({"decision": {"aflow_decision": "no_material_gap", "contract_decision": "no_material_gap", "plan_ready_for_primary_decision": True}, "plan_gaps": [], "lock_payload": {"immutable_user_task_canonical_text": "task", "accepted_plan_canonical_text": "plan", "success_definition_canonical_text": "success", "required_trajectory_canonical_text": "trajectory", "validation_cases_canonical_text": "validations", "fixture_specifications_canonical_text": "fixtures", "hash_algorithm": "sha256"}}, execution_contract_sha256=contract["lock_payload"]["sha256"]))
    worker = command or [sys.executable, "-c", "from pathlib import Path; Path('allowed/result.txt').write_text('ok\\n', encoding='utf-8')"]
    request = {"execution_request": {"contract_path": str(contract_path), "active_lock_path": str(lock_path), "repository_path": str(repo), "baseline": baseline, "worker_command": worker, "output_directory": str(tmp_path / "export"), "cleanup_policy": cleanup}}
    return request, repo, contract_path, lock_path


def test_case_01_invalid_contract_not_admitted(tmp_path):
    request, *_ = prepared(tmp_path); Path(request["execution_request"]["contract_path"]).write_text("{}", encoding="utf-8"); assert launch_execution(request)["execution_status"] == "not_admitted"
def test_case_02_invalid_lock_not_admitted(tmp_path):
    request, *_ = prepared(tmp_path); Path(request["execution_request"]["active_lock_path"]).write_text("{}", encoding="utf-8"); assert launch_execution(request)["execution_status"] == "not_admitted"
def test_case_03_nonready_not_admitted(tmp_path):
    request, *_ = prepared(tmp_path); raw=json.loads(Path(request["execution_request"]["contract_path"]).read_text()); raw["readiness"]["aflow_decision"]="missing_evidence"; Path(request["execution_request"]["contract_path"]).write_text(json.dumps(raw), encoding="utf-8"); assert launch_execution(request)["execution_status"] == "not_admitted"
def test_case_04_admission_failure_creates_no_workspace(tmp_path):
    request, *_ = prepared(tmp_path); request["execution_request"]["baseline"]["manifest_sha256"]="0"*64; assert not (tmp_path / "export").exists() and launch_execution(request)["execution_status"] == "not_admitted"
def test_case_05_exact_commit_baseline_reproduced(tmp_path):
    request, *_ = prepared(tmp_path, baseline_type="commit"); result=launch_execution(request); assert result["baseline_tree_sha256"] and Path(result["workspace"], "allowed/base.txt").read_text()=="base\n"
def test_case_06_dirty_snapshot_reproduced(tmp_path):
    request, _, *_=prepared(tmp_path); result=launch_execution(request); assert Path(result["workspace"],"untracked.txt").read_text()=="untracked\n"
def test_case_07_baseline_mismatch_denied(tmp_path):
    request, *_=prepared(tmp_path); request["execution_request"]["baseline"]["identifier"] = str(tmp_path/"missing.json"); assert launch_execution(request)["execution_status"]=="not_admitted"
def test_case_08_valid_admission_creates_isolation(tmp_path):
    request, repo, *_=prepared(tmp_path); result=launch_execution(request); assert result["execution_status"]=="executed" and Path(result["workspace"]).resolve()!=repo.resolve()
def test_case_09_worker_receives_exact_contract_sha(tmp_path):
    command=[sys.executable,"-c","import os;from pathlib import Path;Path('allowed/hash.txt').write_text(os.environ['AUDISOR_EXECUTION_CONTRACT_SHA256'])"]; request, *_=prepared(tmp_path,command=command); result=launch_execution(request); assert Path(result["workspace"],"allowed/hash.txt").read_text()==result["accepted_contract_sha256"]
def test_case_10_worker_launch_failure_is_not_contradicted(tmp_path):
    request,*_=prepared(tmp_path,command=["definitely-not-a-command"]); result=launch_execution(request); assert (result["execution_status"],result["final_evaluation"])==("launch_failed","not_evaluated")
def test_case_11_authorized_change_captured(tmp_path):
    request,*_=prepared(tmp_path); result=launch_execution(request); assert any(row["path"]=="allowed/result.txt" for row in result["actual_changes"])
def test_case_12_unauthorized_change_contradicted(tmp_path):
    command=[sys.executable,"-c","from pathlib import Path;Path('forbidden').mkdir();Path('forbidden/x').write_text('x')"]; request,*_=prepared(tmp_path,command=command); assert launch_execution(request)["final_evaluation"]=="contradicted"
def test_case_13_change_kinds_detected(tmp_path):
    command=[sys.executable,"-c","from pathlib import Path;p=Path('allowed/base.txt');p.rename('allowed/BASE.txt');Path('allowed/blob.bin').write_bytes(b'\\0x')"]; request,*_=prepared(tmp_path,command=command); changes=launch_execution(request)["actual_changes"]; mode_rows = _changes([{"path":"allowed/mode.txt","kind":"file","mode":420,"sha256":"a"}], [{"path":"allowed/mode.txt","kind":"file","mode":484,"sha256":"a"}]); link_rows = _changes([{"path":"allowed/link","kind":"symlink","mode":511,"target":"one"}], [{"path":"allowed/link","kind":"symlink","mode":511,"target":"two"}]); assert any(row["change"]=="renamed" for row in changes) and any(row.get("binary") for row in changes) and mode_rows[0]["change"]=="mode_changed" and link_rows[0]["change"]=="symlink_changed"
def test_case_14_missing_validation_evidence_unproven(tmp_path):
    request,*_=prepared(tmp_path,include_execution=False); result=launch_execution(request); assert (result["execution_status"],result["final_evaluation"])==("executed","unproven")
def test_case_15_failed_validation_contradicted(tmp_path):
    request,*_=prepared(tmp_path,validation_command=[sys.executable,"-c","raise SystemExit(3)"]); result=launch_execution(request); assert (result["execution_status"],result["final_evaluation"])==("executed","contradicted")
def test_case_16_complete_execution_proven(tmp_path):
    request,*_=prepared(tmp_path); assert launch_execution(request)["final_evaluation"]=="proven"
def test_case_17_export_before_cleanup(tmp_path):
    request,*_=prepared(tmp_path,cleanup="delete_after_verified_export"); result=launch_execution(request); assert result["workspace_cleaned"] and (tmp_path/"export"/"execution-result.json").is_file()
def test_case_18_primary_unchanged(tmp_path):
    request,repo,*_=prepared(tmp_path); before=entries(repo); result=launch_execution(request); assert entries(repo)==before and not result["primary_workspace"]["product_files_mutated"]
def test_case_19_no_result_auto_applied(tmp_path):
    request,repo,*_=prepared(tmp_path); launch_execution(request); assert not (repo/"allowed/result.txt").exists()
def test_case_20_frozen_tree_preserved(tmp_path):
    before=frozen_tree_digest(FROZEN); request,*_=prepared(tmp_path); launch_execution(request); assert frozen_tree_digest(FROZEN)==before
def test_case_21_export_not_hook_state_root(tmp_path):
    """The launcher's output directory is an evidence export, not the hook authority state root."""
    from audisor.audisor_lifecycle.active_state import read_active_state
    request,*_=prepared(tmp_path); result=launch_execution(request); export=tmp_path/"export"
    assert result["execution_status"]=="executed"
    # The exported lock uses the unambiguous evidence name, not the hook state filename
    assert (export/"execution-lock.json").is_file()
    assert not (export/"active-lock.json").exists()
    # The export directory is NOT usable as a hook state root
    assert read_active_state(export) is None
