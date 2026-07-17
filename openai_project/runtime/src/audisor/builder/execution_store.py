"""Atomic execution claims, locks, state transitions, results, and evidence."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from audisor.builder.authority import is_reparse_or_symlink
from audisor.builder.evidence import (
    atomic_write_bytes,
    atomic_write_json,
    canonical_json_bytes,
    sha256_bytes,
)
from audisor.builder.global_authority import (
    AuthorityClaim,
    GlobalAuthorityError,
    GlobalAuthorityRegistry,
)
from audisor.builder.idempotency import IdempotencyIndex
from audisor.builder.scheduler import DeterministicScheduler, SchedulerError
from audisor.builder.terminal_manifest import (
    TaskArtifactPaths,
    TerminalManifestError,
    require_valid_terminal_manifest,
    write_terminal_manifest,
)
from audisor.builder.workspace import WorkspaceManager
from audisor.schemas.build import BuildPlan
from audisor.schemas.execution import (
    ActionExecutionRecord,
    BuildExecutionRequest,
    BuildExecutionState,
    ChangeRecord,
    CommandEvidence,
    TargetAuthorityRecord,
    TargetBaseline,
    TaskExecutionResult,
    WorkerActionPlan,
)


class ExecutionStoreError(RuntimeError):
    """Execution state or evidence could not be persisted safely."""


class ExecutionConflictError(ExecutionStoreError):
    """An execution ID, idempotency key, or active authority conflicts."""


class FileLock:
    """Small cross-process advisory lock held by an open OS file handle."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.handle = None

    def acquire(self, *, blocking: bool, timeout: float = 10.0) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if (self.path.exists() or self.path.is_symlink()) and (
            is_reparse_or_symlink(self.path) or not self.path.is_file()
        ):
            raise ExecutionStoreError("Execution lock path is unsafe")
        self.handle = self.path.open("a+b")
        self.handle.seek(0, os.SEEK_END)
        if self.handle.tell() == 0:
            self.handle.write(b"\0")
            self.handle.flush()
        deadline = time.monotonic() + timeout
        while True:
            try:
                self.handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    mode = msvcrt.LK_NBLCK
                    msvcrt.locking(self.handle.fileno(), mode, 1)
                else:
                    import fcntl

                    flags = fcntl.LOCK_EX | fcntl.LOCK_NB
                    fcntl.flock(self.handle.fileno(), flags)
                return True
            except (OSError, BlockingIOError):
                if not blocking or time.monotonic() >= deadline:
                    self.handle.close()
                    self.handle = None
                    return False
                time.sleep(0.05)

    def release(self) -> None:
        if self.handle is None:
            return
        try:
            self.handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        finally:
            self.handle.close()
            self.handle = None

    def __enter__(self) -> "FileLock":
        if not self.acquire(blocking=True):
            raise ExecutionConflictError("Execution lock is unavailable")
        return self

    def __exit__(self, *_args: object) -> None:
        self.release()


@dataclass
class ExecutionClaim:
    path: Path
    state: BuildExecutionState
    is_new: bool
    lock: FileLock | None

    def close(self) -> None:
        if self.lock is not None:
            self.lock.release()

    def __enter__(self) -> "ExecutionClaim":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


class ExecutionStore:
    """Own durable execution artifacts without modifying preparation files."""

    def __init__(
        self,
        workspace_manager: WorkspaceManager | None = None,
        *,
        data_dir: Path | None = None,
    ) -> None:
        self.workspace_manager = workspace_manager or WorkspaceManager()
        self.data_dir = (data_dir or (Path(tempfile.gettempdir()) / "audisor-data")).resolve()
        self.global_authority = GlobalAuthorityRegistry(self.data_dir)
        self.idempotency = IdempotencyIndex(self.data_dir)

    @staticmethod
    def _state_path(execution_path: Path) -> Path:
        return execution_path / "state.json"

    @staticmethod
    def _prepared_plan_path(execution_path: Path) -> Path:
        return execution_path / "prepared-plan.json"

    @staticmethod
    def _read_json(path: Path) -> object:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            raise ExecutionStoreError("Execution storage is unreadable") from None

    def load_state(self, execution_path: Path) -> BuildExecutionState:
        try:
            return BuildExecutionState.model_validate(
                self._read_json(self._state_path(execution_path))
            )
        except ValidationError:
            raise ExecutionStoreError("Execution state is invalid") from None

    def load_prepared_plan(self, execution_path: Path) -> BuildPlan:
        try:
            return BuildPlan.model_validate(
                self._read_json(self._prepared_plan_path(execution_path))
            )
        except ValidationError:
            raise ExecutionStoreError("Prepared execution plan is invalid") from None

    @staticmethod
    def _scheduler_for(plan: BuildPlan, state: BuildExecutionState) -> DeterministicScheduler:
        scheduler = DeterministicScheduler(plan, state.execution_id)
        scheduler.validate_state(state)
        return scheduler

    @staticmethod
    def _not_valid_state(payload: object) -> BuildExecutionState:
        raw = payload if isinstance(payload, dict) else {}
        tasks: list[dict[str, str]] = []
        for item in raw.get("tasks", []) if isinstance(raw.get("tasks"), list) else []:
            if isinstance(item, dict) and isinstance(item.get("task_id"), str):
                status = item.get("status")
                if status in {"completed", "failed", "blocked", "interrupted"}:
                    tasks.append({"task_id": item["task_id"], "status": status})
        if not tasks:
            tasks = [{"task_id": "invalid-state", "status": "interrupted"}]
        build_id = raw.get("build_id") if isinstance(raw.get("build_id"), str) else "invalid-build"
        execution_id = raw.get("execution_id") if isinstance(raw.get("execution_id"), str) else "invalid-execution"
        try:
            return BuildExecutionState.model_validate(
                {
                    "build_id": build_id,
                    "execution_id": execution_id,
                    "status": "not_valid",
                    "tasks": tasks,
                    "terminal_manifest_sha256": None,
                }
            )
        except ValidationError:
            return BuildExecutionState.model_validate(
                {
                    "build_id": "invalid-build",
                    "execution_id": "invalid-execution",
                    "status": "not_valid",
                    "tasks": [{"task_id": "invalid-state", "status": "interrupted"}],
                }
            )

    def persist_state(self, execution_path: Path, state: BuildExecutionState) -> None:
        try:
            atomic_write_json(
                self._state_path(execution_path), state.model_dump(mode="json")
            )
        except OSError:
            raise ExecutionStoreError("Execution state persistence failed") from None

    def _reconcile_stale(
        self,
        execution_path: Path,
        state: BuildExecutionState,
        prepared_plan: BuildPlan,
    ) -> BuildExecutionState:
        self._scheduler_for(prepared_plan, state)
        reconciled = DeterministicScheduler.interrupt_running(state)
        if reconciled != state:
            self._scheduler_for(prepared_plan, reconciled)
            self.persist_state(execution_path, reconciled)
        return reconciled

    def load_and_reconcile(self, execution_path: Path) -> BuildExecutionState:
        lock = FileLock(execution_path.parent / f".{execution_path.name}.execution.lock")
        try:
            state = self.load_state(execution_path)
            prepared_plan = self.load_prepared_plan(execution_path)
            self._scheduler_for(prepared_plan, state)
        except (ExecutionStoreError, SchedulerError):
            return self._not_valid_state(self._read_json(self._state_path(execution_path)))
        if state.status in {"completed", "failed", "not_valid"}:
            return self.final_state(execution_path, prepared_plan=prepared_plan)
        if not lock.acquire(blocking=False):
            return state
        try:
            return self._reconcile_stale(execution_path, state, prepared_plan)
        finally:
            lock.release()

    @staticmethod
    def _same_authority(
        existing: TargetAuthorityRecord,
        requested: TargetAuthorityRecord,
    ) -> bool:
        return (
            os.path.normcase(existing.resolved_target_root)
            == os.path.normcase(requested.resolved_target_root)
            and [path.casefold() for path in existing.allowed_write_paths]
            == [path.casefold() for path in requested.allowed_write_paths]
        )

    def _existing_records(
        self, executions_root: Path
    ) -> list[tuple[Path, TargetAuthorityRecord, BuildExecutionState]]:
        records: list[tuple[Path, TargetAuthorityRecord, BuildExecutionState]] = []
        if not executions_root.exists() and not executions_root.is_symlink():
            return records
        for path in sorted(executions_root.iterdir(), key=lambda item: item.name.casefold()):
            if path.name.startswith("."):
                continue
            if (
                is_reparse_or_symlink(path)
                or not path.is_dir()
                or path.resolve().parent != executions_root.resolve()
            ):
                raise ExecutionStoreError("Existing execution path is unsafe")
            try:
                authority = TargetAuthorityRecord.model_validate(
                    self._read_json(path / "authority.json")
                )
                state = self.load_state(path)
            except (ExecutionStoreError, ValidationError):
                raise ExecutionStoreError("Existing execution storage is invalid") from None
            records.append((path, authority, state))
        return records

    def claim(
        self,
        *,
        build_path: Path,
        request: BuildExecutionRequest,
        authority: TargetAuthorityRecord,
        baseline: TargetBaseline,
        prepared_plan: BuildPlan,
        initial_state: BuildExecutionState,
        target_root: Path,
    ) -> ExecutionClaim:
        try:
            self._scheduler_for(prepared_plan, initial_state)
        except SchedulerError:
            raise ExecutionStoreError("Initial execution graph is invalid") from None
        executions_root = build_path / "executions"
        if (executions_root.exists() or executions_root.is_symlink()) and (
            is_reparse_or_symlink(executions_root)
            or not executions_root.is_dir()
            or executions_root.resolve().parent != build_path.resolve()
        ):
            raise ExecutionStoreError("Execution storage root is unsafe")
        executions_root.mkdir(parents=True, exist_ok=True)
        if (
            is_reparse_or_symlink(executions_root)
            or executions_root.resolve().parent != build_path.resolve()
        ):
            raise ExecutionStoreError("Execution storage root is unsafe")
        claims_lock = FileLock(executions_root / ".claims.lock")
        if not claims_lock.acquire(blocking=True):
            raise ExecutionConflictError("Execution claims are busy")
        try:
            for path, existing, state in self._existing_records(executions_root):
                try:
                    existing_plan = self.load_prepared_plan(path)
                    self._scheduler_for(existing_plan, state)
                except (ExecutionStoreError, SchedulerError):
                    raise ExecutionStoreError(
                        "Existing execution graph is invalid"
                    ) from None
                if existing.idempotency_key == request.idempotency_key:
                    if existing.request_digest != authority.request_digest:
                        raise ExecutionConflictError(
                            "Idempotency key is already bound to different input"
                        )
                    lock = FileLock(executions_root / f".{path.name}.execution.lock")
                    if not lock.acquire(blocking=False):
                        return ExecutionClaim(path=path, state=state, is_new=False, lock=None)
                    state = self._reconcile_stale(
                        path, state, existing_plan
                    )
                    return ExecutionClaim(path=path, state=state, is_new=False, lock=lock)
                if existing.execution_id == request.execution_id:
                    raise ExecutionConflictError("Execution ID already exists")
                if state.status == "running" and self._same_authority(existing, authority):
                    other_lock = FileLock(
                        executions_root / f".{path.name}.execution.lock"
                    )
                    if not other_lock.acquire(blocking=False):
                        raise ExecutionConflictError(
                            "An execution is already running under this authority"
                        )
                    try:
                        self._reconcile_stale(
                            path, state, existing_plan
                        )
                    finally:
                        other_lock.release()

            final_path = executions_root / request.execution_id
            if final_path.exists() or final_path.is_symlink():
                raise ExecutionConflictError("Execution ID already exists")
            staging = Path(
                tempfile.mkdtemp(
                    prefix=f".{request.execution_id}.",
                    suffix=".tmp",
                    dir=executions_root,
                )
            ).resolve()
            if (
                staging.parent != executions_root.resolve()
                or is_reparse_or_symlink(staging)
            ):
                raise ExecutionStoreError("Execution staging boundary is invalid")
            try:
                (staging / "results").mkdir()
                (staging / "evidence").mkdir()
                workspace_record = self.workspace_manager.create(
                    target_root,
                    staging / "workspace",
                    baseline,
                ).model_copy(
                    update={"workspace_root": str((final_path / "workspace").resolve())}
                )
                atomic_write_json(staging / "request.json", request.model_dump(mode="json"))
                atomic_write_json(
                    staging / "authority.json", authority.model_dump(mode="json")
                )
                atomic_write_json(
                    staging / "baseline.json", baseline.model_dump(mode="json")
                )
                atomic_write_json(
                    staging / "prepared-plan.json",
                    prepared_plan.model_dump(mode="json"),
                )
                atomic_write_json(
                    staging / "workspace.json", workspace_record.model_dump(mode="json")
                )
                atomic_write_json(
                    staging / "state.json", initial_state.model_dump(mode="json")
                )
                staging.rename(final_path)
            except Exception:
                if staging.exists():
                    shutil.rmtree(staging, ignore_errors=True)
                raise

            lock = FileLock(executions_root / f".{request.execution_id}.execution.lock")
            if not lock.acquire(blocking=False):
                raise ExecutionStoreError("New execution lock could not be acquired")
            return ExecutionClaim(
                path=final_path,
                state=initial_state,
                is_new=True,
                lock=lock,
            )
        except ExecutionStoreError:
            raise
        except (OSError, RuntimeError):
            raise ExecutionStoreError("Execution initialization failed") from None
        finally:
            claims_lock.release()

    @staticmethod
    def task_evidence_path(execution_path: Path, task_id: str) -> Path:
        return execution_path / "evidence" / task_id

    def persist_worker_input(
        self, execution_path: Path, task_id: str, worker_input: dict[str, str]
    ) -> None:
        try:
            atomic_write_json(
                self.task_evidence_path(execution_path, task_id) / "worker-input.json",
                worker_input,
            )
        except OSError:
            raise ExecutionStoreError("Worker input persistence failed") from None

    def persist_worker_output(
        self, execution_path: Path, task_id: str, output: object
    ) -> None:
        try:
            atomic_write_json(
                self.task_evidence_path(execution_path, task_id) / "worker-output.json",
                output,
            )
        except OSError:
            raise ExecutionStoreError("Worker output persistence failed") from None

    def persist_action_progress(
        self,
        execution_path: Path,
        task_id: str,
        plan: WorkerActionPlan,
        actions: list[ActionExecutionRecord],
        commands: list[CommandEvidence],
    ) -> None:
        evidence = self.task_evidence_path(execution_path, task_id)
        try:
            atomic_write_json(
                evidence / "actions.json",
                {
                    "requested": plan.model_dump(mode="json"),
                    "executed": [item.model_dump(mode="json") for item in actions],
                },
            )
            atomic_write_json(
                evidence / "commands.json",
                [item.model_dump(mode="json") for item in commands],
            )
        except OSError:
            raise ExecutionStoreError("Action evidence persistence failed") from None

    def persist_terminal_result(
        self,
        execution_path: Path,
        result: TaskExecutionResult,
    ) -> None:
        """Write all terminal evidence, then the result; state changes happen later."""
        evidence = self.task_evidence_path(execution_path, result.task_id)
        try:
            evidence.mkdir(parents=True, exist_ok=True)
            if result.requested_actions is not None:
                atomic_write_json(
                    evidence / "actions.json",
                    {
                        "requested": result.requested_actions.model_dump(mode="json"),
                        "executed": [
                            item.model_dump(mode="json")
                            for item in result.executed_actions
                        ],
                    },
                )
            else:
                atomic_write_json(
                    evidence / "actions.json",
                    {"requested": None, "executed": []},
                )
            atomic_write_json(
                evidence / "commands.json",
                [item.model_dump(mode="json") for item in result.validation_commands],
            )
            atomic_write_json(
                evidence / "changes.json",
                [item.model_dump(mode="json") for item in result.changed_paths],
            )
            atomic_write_json(
                evidence / "validation.json",
                {
                    "acceptable_exit_code": 0,
                    "exit_codes": result.exit_codes,
                    "status": result.status,
                    "commands": [
                        item.model_dump(mode="json")
                        for item in result.validation_commands
                    ],
                },
            )
            atomic_write_json(
                execution_path / "results" / f"{result.task_id}.json",
                result.model_dump(mode="json"),
            )
        except OSError:
            raise ExecutionStoreError("Task result persistence failed") from None

    def finalize_terminal(
        self,
        execution_path: Path,
        state: BuildExecutionState,
        *,
        global_claim: AuthorityClaim,
        prepared_plan: BuildPlan | None = None,
        expected_task_ids: list[str] | None = None,
    ) -> BuildExecutionState:
        """Anchor all terminal artifacts, then publish the manifest-bound state."""
        plan = prepared_plan or self.load_prepared_plan(execution_path)
        try:
            scheduler = self._scheduler_for(plan, state)
            status = scheduler.terminal_status(state)
        except SchedulerError as exc:
            raise ExecutionStoreError("Execution graph is not terminally valid") from exc
        if status not in {"completed", "failed"}:
            raise ExecutionStoreError("Execution is not releasably terminal")
        plan_task_ids = [task.task_id for task in plan.tasks]
        if expected_task_ids is not None and expected_task_ids != plan_task_ids:
            raise ExecutionStoreError("Expected tasks do not match the prepared plan")
        release_evidence = self.global_authority.prepare_release_evidence(
            global_claim, terminal_status=status
        )
        authority_evidence_root = execution_path / "global-authority"
        try:
            atomic_write_bytes(
                authority_evidence_root / "claim.json",
                global_claim.path.read_bytes(),
            )
            atomic_write_bytes(
                authority_evidence_root / "release-evidence.json",
                release_evidence.path.read_bytes(),
            )
        except OSError:
            raise ExecutionStoreError("Global authority evidence persistence failed") from None
        atomic_write_json(
            execution_path / "scheduler.json",
            {
                "build_id": state.build_id,
                "execution_id": state.execution_id,
                "terminal_status": status,
                "tasks": [item.model_dump(mode="json") for item in state.tasks],
            },
        )
        task_artifacts = {
            task_id: TaskArtifactPaths(
                result_path=f"results/{task_id}.json",
                evidence_paths=(f"evidence/{task_id}",),
            )
            for task_id in plan_task_ids
        }
        try:
            manifest = write_terminal_manifest(
                execution_path,
                build_id=state.build_id,
                execution_id=state.execution_id,
                expected_task_ids=plan_task_ids,
                task_artifacts=task_artifacts,
                required_artifacts=(
                    "request.json",
                    "authority.json",
                    "baseline.json",
                    "prepared-plan.json",
                    "workspace.json",
                    "scheduler.json",
                    "global-authority/claim.json",
                    "global-authority/release-evidence.json",
                ),
                authority_artifacts=(
                    "global-authority/claim.json",
                    "global-authority/release-evidence.json",
                ),
            )
            terminal = BuildExecutionState.model_validate(
                {
                    **state.model_dump(mode="json"),
                    "status": status,
                    "terminal_manifest_sha256": manifest.sha256,
                }
            )
            self.persist_state(execution_path, terminal)
            require_valid_terminal_manifest(
                execution_path,
                expected_sha256=manifest.sha256,
                expected_task_ids=plan_task_ids,
            )
            return terminal
        except (OSError, TerminalManifestError, ValidationError) as exc:
            raise ExecutionStoreError("Terminal evidence reconciliation failed") from exc

    def final_state(
        self,
        execution_path: Path,
        *,
        prepared_plan: BuildPlan | None = None,
        expected_task_ids: list[str] | None = None,
    ) -> BuildExecutionState:
        """Return a terminal state only after complete manifest reconciliation."""
        payload = self._read_json(self._state_path(execution_path))
        try:
            state = BuildExecutionState.model_validate(payload)
            plan = prepared_plan or self.load_prepared_plan(execution_path)
            self._scheduler_for(plan, state)
            plan_task_ids = [task.task_id for task in plan.tasks]
            if expected_task_ids is not None and expected_task_ids != plan_task_ids:
                raise SchedulerError("Expected tasks do not match the prepared plan")
        except (ExecutionStoreError, SchedulerError, ValidationError):
            return self._not_valid_state(payload)
        if state.status not in {"completed", "failed"}:
            return state
        try:
            require_valid_terminal_manifest(
                execution_path,
                expected_sha256=state.terminal_manifest_sha256 or "",
                expected_task_ids=plan_task_ids,
            )
            self.global_authority.require_released_terminal_evidence(
                claim_evidence_path=execution_path / "global-authority/claim.json",
                release_evidence_path=(
                    execution_path / "global-authority/release-evidence.json"
                ),
                terminal_manifest_sha256=state.terminal_manifest_sha256 or "",
            )
        except (GlobalAuthorityError, TerminalManifestError):
            return self._not_valid_state(payload)
        return state
