"""Atomic execution initialization, terminal manifests, locks, and recovery."""

import json
from pathlib import Path

import pytest

from audisor.builder.authority import TargetAuthorityResolver
from audisor.builder.execution_store import (
    ExecutionConflictError,
    ExecutionStore,
    ExecutionStoreError,
)
from audisor.builder.scheduler import DeterministicScheduler
from audisor.schemas.build import BuildPlan
from audisor.schemas.execution import BuildExecutionRequest


def prompt() -> str:
    return """## Objective
Create a file.
## Inputs and repository paths
Use the fixture.
## Required work
Create the file.
## Ordered steps
1. Create and validate it.
## Expected output
Return the file.
## Validation
Run validation.
## Evidence to return
Return evidence."""


def plan() -> BuildPlan:
    return BuildPlan.model_validate(
        {
            "build_id": "build-001",
            "status": "ready",
            "gaps": [],
            "tasks": [
                {
                    "task_id": "task-001",
                    "title": "Create file",
                    "depends_on": [],
                    "prompt": prompt(),
                    "expected_outputs": ["src/created.py"],
                    "validation": [
                        {
                            "argv": ["python", "-c", "raise SystemExit(0)"],
                            "working_directory": ".",
                            "acceptable_exit_codes": [0],
                            "timeout_seconds": 30,
                        }
                    ],
                }
            ],
        }
    )


def setup_claim(tmp_path: Path, *, execution_id: str = "execution-001", key: str = "request-001"):
    data = tmp_path / "data"
    build_path = data / "builds" / "build-001"
    build_path.mkdir(parents=True)
    target = tmp_path / "target"
    (target / "src").mkdir(parents=True)
    (target / "tests").mkdir()
    request = BuildExecutionRequest(
        execution_id=execution_id,
        idempotency_key=key,
        target_root=str(target),
        allowed_write_paths=["src", "tests"],
    )
    resolver = TargetAuthorityResolver(
        data_dir=data,
        product_root=tmp_path / "product",
        reference_roots=(tmp_path / "reference",),
        approved_target_roots=(tmp_path,),
    )
    final_workspace = build_path / "executions" / execution_id / "workspace"
    authority = resolver.resolve(
        "build-001",
        request,
        plan_hash="1" * 64,
        integrity_root="2" * 64,
        selected_provider="fake",
        workspace_path=final_workspace,
    )
    scheduler = DeterministicScheduler(plan(), execution_id)
    return (
        ExecutionStore(data_dir=data),
        build_path,
        target,
        request,
        authority,
        scheduler.initial_state(),
    )


def claim_with(parts):
    store, build_path, target, request, authority, state = parts
    return store.claim(
        build_path=build_path,
        request=request,
        authority=authority.record,
        baseline=authority.baseline,
        prepared_plan=plan(),
        initial_state=state,
        target_root=target,
    )


def claim_global_authority(parts):
    store, _build_path, target, request, authority, _state = parts
    return store.global_authority.acquire(
        build_id="build-001",
        execution_id=request.execution_id,
        idempotency_key=request.idempotency_key,
        request_fingerprint=authority.record.request_digest,
        target_root=target,
        allowed_paths=tuple(Path(path) for path in authority.record.allowed_resolved_paths),
    )


def release_global_authority(store, claim, terminal) -> None:
    release_evidence = store.global_authority.prepare_release_evidence(
        claim, terminal_status=terminal.status
    )
    store.global_authority.release(
        claim,
        terminal_status=terminal.status,
        terminal_manifest_sha256=terminal.terminal_manifest_sha256,
        release_evidence_sha256=release_evidence.sha256,
        reconciliation_verified=True,
    )


def persist_terminal_artifacts(execution_path: Path, task_id: str = "task-001") -> None:
    result = execution_path / "results" / f"{task_id}.json"
    evidence = execution_path / "evidence" / task_id / "validation.json"
    evidence.parent.mkdir(parents=True, exist_ok=True)
    result.write_text(
        json.dumps({"task_id": task_id, "status": "completed"}) + "\n",
        encoding="utf-8",
    )
    evidence.write_text(
        json.dumps({"task_id": task_id, "status": "completed", "exit_codes": [0]})
        + "\n",
        encoding="utf-8",
    )


def test_new_claim_persists_authority_baseline_workspace_and_state(tmp_path: Path) -> None:
    parts = setup_claim(tmp_path)
    with claim_with(parts) as claim:
        assert claim.is_new is True
        for filename in (
            "request.json",
            "authority.json",
            "baseline.json",
            "prepared-plan.json",
            "workspace.json",
            "state.json",
        ):
            assert (claim.path / filename).is_file()
        assert (claim.path / "workspace/src").is_dir()
        assert (claim.path / "results").is_dir()
        assert (claim.path / "evidence").is_dir()


def test_claim_rejects_preexisting_execution_symlink_when_supported(
    tmp_path: Path,
) -> None:
    parts = setup_claim(tmp_path)
    _store, build_path, _target, _request, _authority, _state = parts
    executions = build_path / "executions"
    executions.mkdir()
    outside = tmp_path / "outside-execution"
    outside.mkdir()
    link = executions / "execution-preexisting"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is unavailable")
    with pytest.raises(ExecutionStoreError, match="unsafe"):
        claim_with(parts)
    assert list(outside.iterdir()) == []


def test_identical_idempotent_claim_returns_existing_without_new_workspace(tmp_path: Path) -> None:
    parts = setup_claim(tmp_path)
    with claim_with(parts) as first:
        first_path = first.path
    with claim_with(parts) as second:
        assert second.is_new is False
        assert second.path == first_path
        assert second.state.status == "interrupted"
    assert len([path for path in first_path.parent.iterdir() if path.is_dir()]) == 1


def test_reused_idempotency_key_with_different_input_is_rejected(tmp_path: Path) -> None:
    first = setup_claim(tmp_path)
    with claim_with(first):
        pass
    store, build_path, target, _request, _authority, _state = first
    other = BuildExecutionRequest(
        execution_id="execution-002",
        idempotency_key="request-001",
        target_root=str(target),
        allowed_write_paths=["src"],
    )
    resolver = TargetAuthorityResolver(
        data_dir=tmp_path / "data",
        product_root=tmp_path / "product",
        reference_roots=(tmp_path / "reference",),
        approved_target_roots=(tmp_path,),
    )
    resolved = resolver.resolve(
        "build-001",
        other,
        plan_hash="1" * 64,
        integrity_root="2" * 64,
        selected_provider="fake",
        workspace_path=build_path / "executions/execution-002/workspace",
    )
    with pytest.raises(ExecutionConflictError, match="Idempotency"):
        store.claim(
            build_path=build_path,
            request=other,
            authority=resolved.record,
            baseline=resolved.baseline,
            prepared_plan=plan(),
            initial_state=DeterministicScheduler(plan(), "execution-002").initial_state(),
            target_root=target,
        )


def test_duplicate_execution_id_with_different_key_is_rejected(tmp_path: Path) -> None:
    first = setup_claim(tmp_path)
    with claim_with(first):
        pass
    store, build_path, target, _request, _authority, _state = first
    duplicate = BuildExecutionRequest(
        execution_id="execution-001",
        idempotency_key="other-key",
        target_root=str(target),
        allowed_write_paths=["src", "tests"],
    )
    resolver = TargetAuthorityResolver(
        data_dir=tmp_path / "data",
        product_root=tmp_path / "product",
        reference_roots=(tmp_path / "reference",),
        approved_target_roots=(tmp_path,),
    )
    resolved = resolver.resolve(
        "build-001",
        duplicate,
        plan_hash="1" * 64,
        integrity_root="2" * 64,
        selected_provider="fake",
        workspace_path=build_path / "executions/execution-001/workspace",
    )
    with pytest.raises(ExecutionConflictError, match="Execution ID"):
        store.claim(
            build_path=build_path,
            request=duplicate,
            authority=resolved.record,
            baseline=resolved.baseline,
            prepared_plan=plan(),
            initial_state=DeterministicScheduler(plan(), "execution-001").initial_state(),
            target_root=target,
        )


def test_unlocked_stale_running_state_becomes_interrupted(tmp_path: Path) -> None:
    parts = setup_claim(tmp_path)
    store = parts[0]
    with claim_with(parts) as claim:
        state = claim.state.model_copy(
            update={
                "tasks": [claim.state.tasks[0].model_copy(update={"status": "running"})]
            }
        )
        store.persist_state(claim.path, state)
        execution_path = claim.path
    loaded = store.load_and_reconcile(execution_path)
    assert loaded.status == "interrupted"
    assert loaded.tasks[0].status == "interrupted"


def test_active_held_execution_lock_returns_durable_running_state(tmp_path: Path) -> None:
    parts = setup_claim(tmp_path)
    with claim_with(parts) as first:
        with claim_with(parts) as second:
            assert second.is_new is False
            assert second.lock is None
            assert second.state.status == "running"


def test_different_execution_cannot_claim_same_actively_locked_authority(
    tmp_path: Path,
) -> None:
    parts = setup_claim(tmp_path)
    store, build_path, target, _request, _authority, _state = parts
    with claim_with(parts):
        second_request = BuildExecutionRequest(
            execution_id="execution-002",
            idempotency_key="request-002",
            target_root=str(target),
            allowed_write_paths=["src", "tests"],
        )
        resolver = TargetAuthorityResolver(
            data_dir=tmp_path / "data",
            product_root=tmp_path / "product",
            reference_roots=(tmp_path / "reference",),
            approved_target_roots=(tmp_path,),
        )
        resolved = resolver.resolve(
            "build-001",
            second_request,
            plan_hash="1" * 64,
            integrity_root="2" * 64,
            selected_provider="fake",
            workspace_path=build_path / "executions/execution-002/workspace",
        )
        with pytest.raises(ExecutionConflictError, match="already running"):
            store.claim(
                build_path=build_path,
                request=second_request,
                authority=resolved.record,
                baseline=resolved.baseline,
                prepared_plan=plan(),
                initial_state=DeterministicScheduler(
                    plan(), "execution-002"
                ).initial_state(),
                target_root=target,
            )


def test_terminal_state_is_manifest_bound_and_reloads_as_trusted(tmp_path: Path) -> None:
    parts = setup_claim(tmp_path)
    store = parts[0]
    scheduler = DeterministicScheduler(plan(), "execution-001")
    global_claim = claim_global_authority(parts)
    with claim_with(parts) as claim:
        persist_terminal_artifacts(claim.path)
        state = scheduler.mark_completed(
            scheduler.mark_running(claim.state, "task-001"), "task-001"
        )
        terminal = store.finalize_terminal(
            claim.path,
            state,
            global_claim=global_claim,
            prepared_plan=plan(),
            expected_task_ids=["task-001"],
        )

        assert terminal.status == "completed"
        assert terminal.terminal_manifest_sha256 is not None
        assert (claim.path / "terminal-manifest.json").is_file()
        assert (claim.path / "global-authority/claim.json").is_file()
        assert (claim.path / "global-authority/release-evidence.json").is_file()
        manifest_payload = json.loads(
            (claim.path / "terminal-manifest.json").read_text(encoding="utf-8")
        )
        manifest_paths = {
            artifact["path"] for artifact in manifest_payload["artifacts"]
        }
        assert "global-authority/claim.json" in manifest_paths
        assert "global-authority/release-evidence.json" in manifest_paths
        assert store.final_state(claim.path).status == "not_valid"
        release_global_authority(store, global_claim, terminal)
        assert store.final_state(
            claim.path,
            prepared_plan=plan(),
            expected_task_ids=["task-001"],
        ) == terminal


def test_terminal_reload_reconciles_missing_evidence_to_not_valid(tmp_path: Path) -> None:
    parts = setup_claim(tmp_path)
    store = parts[0]
    scheduler = DeterministicScheduler(plan(), "execution-001")
    global_claim = claim_global_authority(parts)
    with claim_with(parts) as claim:
        persist_terminal_artifacts(claim.path)
        state = scheduler.mark_completed(
            scheduler.mark_running(claim.state, "task-001"), "task-001"
        )
        terminal = store.finalize_terminal(
            claim.path,
            state,
            global_claim=global_claim,
            prepared_plan=plan(),
            expected_task_ids=["task-001"],
        )
        release_global_authority(store, global_claim, terminal)
        (claim.path / "evidence/task-001/validation.json").unlink()

        reconciled = store.final_state(
            claim.path, expected_task_ids=["task-001"]
        )

        assert terminal.status == "completed"
        assert reconciled.status == "not_valid"


@pytest.mark.parametrize(
    "tasks",
    [
        [{"task_id": "unknown-task", "status": "ready"}],
        [
            {"task_id": "task-001", "status": "ready"},
            {"task_id": "TASK-001", "status": "pending"},
        ],
    ],
)
def test_durable_load_rejects_unknown_or_case_duplicate_task_graph(
    tmp_path: Path,
    tasks: list[dict[str, str]],
) -> None:
    parts = setup_claim(tmp_path)
    store = parts[0]
    with claim_with(parts) as claim:
        (claim.path / "state.json").write_text(
            json.dumps(
                {
                    "build_id": "build-001",
                    "execution_id": "execution-001",
                    "status": "running",
                    "tasks": tasks,
                    "terminal_manifest_sha256": None,
                }
            )
            + "\n",
            encoding="utf-8",
        )

        reconciled = store.load_and_reconcile(claim.path)

        assert reconciled.status == "not_valid"
        assert reconciled.terminal_manifest_sha256 is None


def test_claimed_terminal_state_without_manifest_is_not_trusted(tmp_path: Path) -> None:
    parts = setup_claim(tmp_path)
    store = parts[0]
    with claim_with(parts) as claim:
        (claim.path / "state.json").write_text(
            json.dumps(
                {
                    "build_id": "build-001",
                    "execution_id": "execution-001",
                    "status": "completed",
                    "tasks": [{"task_id": "task-001", "status": "completed"}],
                    "terminal_manifest_sha256": "f" * 64,
                }
            )
            + "\n",
            encoding="utf-8",
        )

        reconciled = store.final_state(
            claim.path, expected_task_ids=["task-001"]
        )

        assert reconciled.status == "not_valid"
