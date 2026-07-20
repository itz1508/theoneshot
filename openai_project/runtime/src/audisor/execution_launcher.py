"""Fail-closed external launcher for already locked Audisor contracts.

This module deliberately has no plan, Audisor, assembly, acceptance, or lock
creation entrypoint.  It consumes those artifacts, reproduces their accepted
baseline outside the primary repository, and records only observable facts.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from audisor.audisor_lifecycle.adapter import verify_contract
from audisor.audisor_lifecycle.contract import canonical_text, verify_lock


class LauncherError(RuntimeError):
    """Raised for a malformed external launcher request."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise LauncherError(f"JSON artifact is unreadable: {path}") from error
    if not isinstance(value, dict):
        raise LauncherError(f"JSON artifact must be an object: {path}")
    return value


def _relative(value: object) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise LauncherError("A relative path is malformed")
    path = PurePosixPath(value.replace("\\", "/"))
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise LauncherError("A relative path escapes its root")
    return path.as_posix()


def _inside(path: str, root: str) -> bool:
    candidate, parent = PurePosixPath(path), PurePosixPath(root)
    return candidate == parent or parent in candidate.parents


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.resolve(strict=False).relative_to(parent.resolve(strict=False))
        return True
    except ValueError:
        return False


def _tree_entries(root: Path) -> list[dict[str, Any]]:
    """Return a deterministic inventory including kinds, modes, links, and bytes."""
    entries: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*"), key=lambda item: (item.as_posix().casefold(), item.as_posix())):
        relative = path.relative_to(root).as_posix()
        if relative == ".git" or relative.startswith(".git/"):
            continue
        info = path.lstat()
        mode = stat.S_IMODE(info.st_mode)
        if path.is_symlink():
            entries.append({"path": relative, "kind": "symlink", "mode": mode, "target": os.readlink(path)})
        elif path.is_dir():
            entries.append({"path": relative, "kind": "directory", "mode": mode})
        elif path.is_file():
            data = path.read_bytes()
            entries.append({"path": relative, "kind": "file", "mode": mode, "sha256": _sha256(data), "size": len(data), "binary": b"\0" in data})
        else:
            raise LauncherError(f"Unsupported baseline entry: {relative}")
    return entries


def _tree_digest(entries: list[dict[str, Any]]) -> str:
    return _sha256(canonical_text(entries).encode("utf-8"))


def _entry_table(entries: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(entry["path"]): entry for entry in entries}


def _write_json(path: Path, value: Mapping[str, Any] | list[Any]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    path.write_text(payload, encoding="utf-8", newline="\n")
    return _sha256(payload.encode("utf-8"))


def _admission(request: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], Path, Path, Path, Path]:
    if set(request) != {"execution_request"} or not isinstance(request["execution_request"], Mapping):
        raise LauncherError("Request must contain exactly execution_request")
    data = dict(request["execution_request"])
    required = {"contract_path", "active_lock_path", "repository_path", "baseline", "worker_command", "output_directory", "cleanup_policy"}
    if set(data) != required:
        raise LauncherError("Execution request fields are incomplete or unknown")
    if data["cleanup_policy"] not in {"preserve", "delete_after_verified_export"}:
        raise LauncherError("cleanup_policy is invalid")
    if not isinstance(data["worker_command"], list) or not data["worker_command"] or any(not isinstance(item, str) or not item for item in data["worker_command"]):
        raise LauncherError("worker_command must be a non-empty argv list")
    baseline = data["baseline"]
    if not isinstance(baseline, Mapping) or set(baseline) != {"type", "identifier", "manifest_sha256"}:
        raise LauncherError("baseline is incomplete")
    if baseline["type"] not in {"commit", "working_tree_snapshot"} or not isinstance(baseline["identifier"], str) or not isinstance(baseline["manifest_sha256"], str):
        raise LauncherError("baseline is malformed")
    repository = Path(str(data["repository_path"])).resolve(strict=True)
    contract_path = Path(str(data["contract_path"])).resolve(strict=True)
    lock_path = Path(str(data["active_lock_path"])).resolve(strict=True)
    output = Path(str(data["output_directory"])).resolve(strict=False)
    if not repository.is_dir() or _is_within(output, repository):
        raise LauncherError("output_directory must be outside the primary repository")
    contract, lock = _read_json(contract_path), _read_json(lock_path)
    if not verify_contract(contract):
        raise LauncherError("contract verification failed")
    if not verify_lock(lock):
        raise LauncherError("primary lock verification failed")
    readiness = contract.get("readiness")
    gate = readiness.get("execution_permitted_when") if isinstance(readiness, Mapping) else None
    required_gates = {"aflow_decision_is_no_material_gap", "contract_decision_is_no_material_gap", "unresolved_items_empty", "schema_valid", "references_valid", "traceability_valid", "authority_valid", "canonicalization_valid", "lock_valid", "drift_absent"}
    if not isinstance(readiness, Mapping) or readiness.get("aflow_decision") != "no_material_gap" or readiness.get("contract_decision") != "no_material_gap" or readiness.get("unresolved_items") != [] or not isinstance(gate, Mapping) or any(gate.get(name) is not True for name in required_gates):
        raise LauncherError("contract is not execution-ready")
    contract_sha = contract.get("lock_payload", {}).get("sha256")
    if lock.get("canonical_payload", {}).get("execution_contract_sha256") != contract_sha:
        raise LauncherError("primary lock does not bind the exact contract SHA-256")
    accepted = contract.get("accepted_task_input", {})
    if not isinstance(accepted, Mapping) or accepted.get("accepted_baseline") != dict(baseline) or accepted.get("drift_state") != "valid":
        raise LauncherError("supplied baseline or drift state does not match the accepted contract")
    return data, contract, lock, repository, contract_path, lock_path, output


def _copy_snapshot(manifest_path: Path, expected_sha: str, workspace: Path) -> str:
    raw = manifest_path.read_bytes()
    if _sha256(raw) != expected_sha:
        raise LauncherError("working-tree snapshot manifest SHA-256 does not match")
    manifest = _read_json(manifest_path)
    if set(manifest) != {"source_root", "entries", "tree_sha256"} or not isinstance(manifest["entries"], list):
        raise LauncherError("working-tree snapshot manifest is malformed")
    source = Path(str(manifest["source_root"])).resolve(strict=True)
    entries = manifest["entries"]
    if _tree_digest(entries) != manifest["tree_sha256"]:
        raise LauncherError("working-tree snapshot manifest tree digest is invalid")
    workspace.mkdir(parents=True, exist_ok=False)
    for entry in sorted(entries, key=lambda row: (str(row["path"]).count("/"), str(row["path"]))):
        relative = _relative(entry.get("path")); destination = workspace.joinpath(*PurePosixPath(relative).parts)
        kind = entry.get("kind")
        if kind == "directory":
            destination.mkdir(parents=True, exist_ok=True)
        elif kind == "file":
            source_file = source.joinpath(*PurePosixPath(relative).parts)
            if not source_file.is_file() or source_file.is_symlink():
                raise LauncherError("snapshot source file is missing or unsafe")
            content = source_file.read_bytes()
            if entry.get("sha256") != _sha256(content):
                raise LauncherError("snapshot source file differs from accepted manifest")
            destination.parent.mkdir(parents=True, exist_ok=True); destination.write_bytes(content)
        elif kind == "symlink":
            destination.parent.mkdir(parents=True, exist_ok=True); os.symlink(entry.get("target"), destination)
        else:
            raise LauncherError("snapshot manifest entry kind is invalid")
        if isinstance(entry.get("mode"), int) and not destination.is_symlink():
            os.chmod(destination, entry["mode"])
    digest = _tree_digest(_tree_entries(workspace))
    if digest != manifest["tree_sha256"]:
        raise LauncherError("isolated working-tree snapshot does not match accepted manifest")
    return digest


def _create_workspace(data: Mapping[str, Any], repository: Path, workspace: Path) -> str:
    baseline = data["baseline"]
    if baseline["type"] == "working_tree_snapshot":
        return _copy_snapshot(Path(baseline["identifier"]).resolve(strict=True), baseline["manifest_sha256"], workspace)
    completed = subprocess.run(["git", "-C", str(repository), "worktree", "add", "--detach", str(workspace), baseline["identifier"]], text=True, capture_output=True, check=False)
    if completed.returncode != 0:
        raise LauncherError("exact commit worktree creation failed")
    head = subprocess.run(["git", "-C", str(workspace), "rev-parse", "HEAD"], text=True, capture_output=True, check=False)
    if head.returncode != 0 or head.stdout.strip() != baseline["identifier"]:
        raise LauncherError("isolated worktree commit does not match accepted baseline")
    digest = _tree_digest(_tree_entries(workspace))
    if digest != baseline["manifest_sha256"]:
        raise LauncherError("isolated commit worktree manifest does not match accepted baseline")
    return digest


def _changes(before: list[dict[str, Any]], after: list[dict[str, Any]]) -> list[dict[str, Any]]:
    old, new = _entry_table(before), _entry_table(after)
    rows: list[dict[str, Any]] = []
    removed = [path for path in old if path not in new and old[path]["kind"] == "file"]
    created = [path for path in new if path not in old and new[path]["kind"] == "file"]
    renamed: dict[str, str] = {}
    for source in removed:
        match = next((target for target in created if new[target].get("sha256") == old[source].get("sha256")), None)
        if match:
            renamed[source] = match; created.remove(match)
    for path in sorted(set(old) | set(new), key=lambda item: (item.casefold(), item)):
        if path in renamed:
            target = new[renamed[path]]
            rows.append({"change": "renamed", "path": renamed[path], "from_path": path, "sha256": old[path].get("sha256"), "mode_changed": old[path].get("mode") != target.get("mode")}); continue
        if path in renamed.values() or old.get(path) == new.get(path):
            continue
        if path not in old:
            rows.append({"change": "created", "path": path, "untracked": True, "kind": new[path]["kind"], "sha256": new[path].get("sha256"), "binary": new[path].get("binary", False)})
        elif path not in new:
            rows.append({"change": "deleted", "path": path, "kind": old[path]["kind"], "sha256": old[path].get("sha256")})
        else:
            changed = "modified"
            if old[path]["kind"] == "symlink" or new[path]["kind"] == "symlink": changed = "symlink_changed"
            elif old[path].get("mode") != new[path].get("mode"): changed = "mode_changed"
            rows.append({"change": changed, "path": path, "before": old[path], "after": new[path], "binary": bool(old[path].get("binary") or new[path].get("binary"))})
    for first in old:
        for second in new:
            if first != second and first.casefold() == second.casefold() and first not in new and second not in old:
                rows.append({"change": "case_only_rename", "path": second, "from_path": first})
    return rows


def _authorized(changes: list[dict[str, Any]], contract: Mapping[str, Any]) -> list[str]:
    authority = contract["authority"]; allowed = authority["allowed_paths"]; prohibited = authority["prohibited_paths"]
    actions = contract["implementation_plan"]
    violations: list[str] = []
    for change in changes:
        paths = [change["path"]] + ([change["from_path"]] if "from_path" in change else [])
        for path in paths:
            if not any(_inside(path, root) for root in allowed) or any(_inside(path, root) for root in prohibited):
                violations.append(path); continue
            matching = [action for action in actions if any(_inside(path, target) for target in action["target_paths"])]
            if not matching:
                violations.append(path); continue
            permitted = {kind for action in matching for kind in action.get("authorized_mutation_types", [])}
            kind = change["change"].replace("_changed", "").replace("case_only_rename", "rename")
            if kind not in permitted:
                violations.append(path)
    return sorted(set(violations))


def _run(command: list[str], cwd: Path, timeout: int, env: Mapping[str, str]) -> dict[str, Any]:
    started = _now()
    try:
        process = subprocess.run(command, cwd=cwd, env=dict(env), text=True, capture_output=True, timeout=timeout, check=False)
        return {"command": command, "working_directory": str(cwd), "started_at": started, "completed_at": _now(), "exit_code": process.returncode, "stdout": process.stdout, "stderr": process.stderr}
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"command": command, "working_directory": str(cwd), "started_at": started, "completed_at": _now(), "exit_code": None, "stdout": "", "stderr": str(error), "launch_error": True}


def _validations(contract: Mapping[str, Any], workspace: Path, env: Mapping[str, str], output: Path) -> tuple[list[dict[str, Any]], list[str], bool]:
    authority = contract["authority"]
    records: list[dict[str, Any]] = []; missing: list[str] = []; failed = False
    for validation in contract["validation_contract"]:
        spec = validation.get("execution")
        if not isinstance(spec, Mapping):
            missing.append(f"validation {validation['validation_id']} has no executable definition"); continue
        command, cwd = spec.get("command"), spec.get("working_directory", ".")
        if "run_command" not in authority.get("allowed_tools", []) or "run_command" in authority.get("prohibited_tools", []) or spec.get("network") is not False or not isinstance(command, list) or not command or any(not isinstance(part, str) or not part for part in command):
            missing.append(f"validation {validation['validation_id']} is not permitted"); continue
        relative_cwd = "." if cwd == "." else _relative(cwd)
        resolved = workspace if relative_cwd == "." else workspace.joinpath(*PurePosixPath(relative_cwd).parts)
        if not _is_within(resolved, workspace) or not resolved.is_dir():
            missing.append(f"validation {validation['validation_id']} has an invalid working directory"); continue
        timeout = spec.get("timeout_seconds")
        if not isinstance(timeout, int) or not 1 <= timeout <= 300:
            missing.append(f"validation {validation['validation_id']} has an invalid timeout"); continue
        record = _run(command, resolved, timeout, env); record["validation_id"] = validation["validation_id"]
        outputs = spec.get("evidence_outputs", [])
        if not isinstance(outputs, list) or any(not isinstance(item, str) for item in outputs):
            missing.append(f"validation {validation['validation_id']} has invalid evidence outputs")
        else:
            artifacts = []
            for relative in outputs:
                path = output / _relative(relative)
                if path.is_file(): artifacts.append({"path": relative, "sha256": _sha256(path.read_bytes())})
                else: missing.append(f"validation {validation['validation_id']} evidence output is missing: {relative}")
            record["produced_artifacts"] = artifacts
        if record["exit_code"] != 0: failed = True
        records.append(record)
    return records, missing, failed


def launch_execution(request: Mapping[str, Any]) -> dict[str, Any]:
    """Run one existing locked contract; all admission failures are side-effect free."""
    try:
        data, contract, lock, repository, contract_path, lock_path, output = _admission(request)
    except LauncherError as error:
        return {"execution_status": "not_admitted", "final_evaluation": "not_evaluated", "errors": [str(error)]}
    primary_before = _tree_entries(repository)
    output.mkdir(parents=True, exist_ok=True)
    workspace = Path(tempfile.mkdtemp(prefix="audisor-launch-", dir=output.parent)); workspace.rmdir()
    base = {"contract": contract["lock_payload"]["sha256"], "contract_file": _sha256(contract_path.read_bytes()), "lock_file": _sha256(lock_path.read_bytes())}
    try:
        baseline_digest = _create_workspace(data, repository, workspace)
    except (LauncherError, OSError) as error:
        return {"execution_status": "launch_failed", "final_evaluation": "not_evaluated", "errors": [str(error)]}
    _write_json(output / "accepted-contract.json", contract); _write_json(output / "active-lock.json", lock)
    env = {**os.environ, "AUDISOR_EXECUTION_CONTRACT_PATH": str((output / "accepted-contract.json").resolve()), "AUDISOR_EXECUTION_CONTRACT_SHA256": str(base["contract"]), "AUDISOR_AUTHORIZED_PATHS": json.dumps(contract["authority"]["allowed_paths"]), "AUDISOR_OUTPUT_DIRECTORY": str(output.resolve())}
    before = _tree_entries(workspace)
    worker = _run(data["worker_command"], workspace, 300, env)
    after = _tree_entries(workspace)
    changes = _changes(before, after); violations = _authorized(changes, contract)
    validations, missing, validation_failed = _validations(contract, workspace, env, output)
    primary_after = _tree_entries(repository)
    contract_or_lock_changed = base != {"contract": contract["lock_payload"]["sha256"], "contract_file": _sha256(contract_path.read_bytes()), "lock_file": _sha256(lock_path.read_bytes())}
    result: dict[str, Any] = {
        "execution_status": "executed", "final_evaluation": "proven", "accepted_contract_sha256": base["contract"], "baseline_tree_sha256": baseline_digest,
        "worker": worker, "actual_changes": changes, "authority_violations": violations, "validation_results": validations, "missing_evidence": missing,
        "actual_trajectory": [{"observed_event_id": "worker", "related_stage_id": contract["execution_trajectory"][0]["stage_id"], "operation": "worker_command", "working_directory": str(workspace), "affected_paths": [row["path"] for row in changes], "started_at": worker["started_at"], "completed_at": worker["completed_at"], "exit_code": worker["exit_code"], "produced_evidence": []}] + [{"observed_event_id": row["validation_id"], "related_stage_id": None, "operation": "validation", "working_directory": row["working_directory"], "affected_paths": [], "started_at": row["started_at"], "completed_at": row["completed_at"], "exit_code": row["exit_code"], "produced_evidence": row.get("produced_artifacts", [])} for row in validations],
        "primary_workspace": {"before_manifest": _tree_digest(primary_before), "after_manifest": _tree_digest(primary_after), "product_files_mutated": primary_before != primary_after, "runtime_state_files_written": []},
    }
    if worker.get("launch_error"):
        result.update(execution_status="launch_failed", final_evaluation="not_evaluated")
    elif violations or contract_or_lock_changed or result["primary_workspace"]["product_files_mutated"] or validation_failed:
        result["final_evaluation"] = "contradicted"
    elif missing:
        result["final_evaluation"] = "unproven"
    artifact_hashes = {name: _write_json(output / name, value) for name, value in {"actual-changes.json": changes, "validations.json": validations, "primary-workspace.json": result["primary_workspace"]}.items()}
    result["evidence_hashes"] = artifact_hashes
    result_hash = _write_json(output / "execution-result.json", result); result["execution_result_sha256"] = result_hash
    if data["cleanup_policy"] == "delete_after_verified_export" and (output / "execution-result.json").is_file():
        shutil.rmtree(workspace, ignore_errors=True); result["workspace_cleaned"] = not workspace.exists()
    else:
        result["workspace"] = str(workspace)
    return result
