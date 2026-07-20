from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any, Callable

from dulwich import porcelain
from dulwich.errors import NotGitRepository
from dulwich.repo import Repo

from audisor.builder.store import BuildStore
from audisor.builder.task_loader import (
    PreparedBuildBlockedError,
    PreparedBuildIntegrityError,
    PreparedBuildLoader,
    PreparedBuildNotFoundError,
)
from audisor.operations.models import ClientMetadata, OperationRequest, OperationResponse, BuildOperationInput
from audisor.operations.store import ContinuationClaimError, SharedOperationStore
from audisor.schemas.execution import BuildExecutionRequest

from .handoff import build_handoff, persist_handoff, persist_launch_result
from .analysis_request import build_analysis_request
from .launcher import CodexLaunchError, launch_codex
from .models import CodexRunResult, PreparedBuildContext


class CodexAdapterError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code


def _operation_id(build_id: str) -> str:
    return f"codex-{build_id}-{uuid.uuid4().hex[:16]}"


class CodexAdapter:
    """Project one persisted prepared Build into exactly one Codex launch."""

    def __init__(
        self,
        *,
        build_store: BuildStore | None = None,
        operation_store: SharedOperationStore | None = None,
        operation_service: Any | None = None,
        loader: PreparedBuildLoader | None = None,
        launcher: Callable[..., tuple[int | None, int, str, tuple[str, ...]]] = launch_codex,
    ) -> None:
        self.build_store = build_store or BuildStore.from_environment()
        self.operation_store = operation_store or SharedOperationStore(
            Path(os.environ.get("AUDISOR_OPERATION_DATA_DIR", Path.home() / ".audisor" / "operations"))
        )
        self.loader = loader or PreparedBuildLoader(self.build_store)
        self.operation_service = operation_service
        self.launcher = launcher

    def _prepared(self, build_id: str) -> tuple[Any, PreparedBuildContext]:
        try:
            prepared = self.loader.load(build_id)
        except PreparedBuildNotFoundError as exc:
            raise CodexAdapterError("prepared_build_not_found", str(exc)) from exc
        except PreparedBuildBlockedError as exc:
            raise CodexAdapterError("prepared_build_not_accepted", str(exc)) from exc
        except PreparedBuildIntegrityError as exc:
            raise CodexAdapterError("prepared_build_invalid", str(exc)) from exc
        context = prepared.instruction.execution_context
        if context is None:
            raise CodexAdapterError("prepared_build_contract_incomplete", "Prepared Build has no execution context")
        target = Path(context.target_root).expanduser()
        if not target.exists() or not target.is_dir():
            raise CodexAdapterError("prepared_build_repository_mismatch", "Prepared target root is unavailable")
        target = target.resolve()
        root_reference = context.repository_identity.get("root_reference", "")
        if not root_reference:
            raise CodexAdapterError("prepared_build_repository_mismatch", "Repository identity is incomplete")
        try:
            repository = Repo.discover(target)
            discovered_root = Path(repository.path).resolve()
            repository.close()
            if discovered_root != Path(root_reference).expanduser().resolve():
                raise CodexAdapterError("prepared_build_repository_mismatch", "Prepared repository identity does not match")
            expected_dirty = context.repository_identity.get("dirty_state")
            status = porcelain.status(Repo(str(discovered_root)), ignored=False, untracked_files="all")
            dirty = bool(status.staged or status.unstaged or status.untracked)
            if expected_dirty in {"clean", "dirty"} and dirty != (expected_dirty == "dirty"):
                raise CodexAdapterError("prepared_build_repository_mismatch", "Prepared dirty-state identity does not match")
        except NotGitRepository as exc:
            raise CodexAdapterError("prepared_build_repository_mismatch", "Prepared target is not the recorded repository") from exc
        except CodexAdapterError:
            raise
        except Exception as exc:
            raise CodexAdapterError("prepared_build_repository_mismatch", "Prepared repository identity could not be verified") from exc
        return prepared, PreparedBuildContext(
            build_id=build_id,
            build_path=prepared.build_path,
            target_root=target.resolve(),
            allowed_write_paths=tuple(context.allowed_write_paths),
            context=context.model_dump(mode="json"),
        )

    def _request(self, prepared: Any, operation_id: str) -> OperationRequest:
        context = prepared.instruction.execution_context
        assert context is not None
        request = BuildExecutionRequest(
            execution_id=operation_id,
            idempotency_key=operation_id,
            target_root=context.target_root,
            allowed_write_paths=list(context.allowed_write_paths),
            aflow_analysis_request=build_analysis_request(operation_id=operation_id, prepared=prepared),
        )
        return OperationRequest(
            operation_id=operation_id,
            operation_kind="build",
            client=ClientMetadata(
                client_id="codex",
                adapter_id="audisor.codex",
                adapter_version="1.0",
                client_version=None,
                capabilities=("build",),
            ),
            repository={
                **dict(context.repository_identity),
                "authority_limits": dict(context.authority_limits),
            },
            requested_scope={
                "target_root": context.target_root,
                "allowed_write_paths": list(context.allowed_write_paths),
            },
            build=BuildOperationInput(prepared.instruction.build_id, request),
        )

    @staticmethod
    def _contract_reference(prepared: Any, operation_id: str, response: OperationResponse) -> str | None:
        if isinstance(response.execution_contract_reference, str):
            candidate = Path(response.execution_contract_reference)
            try:
                inside_build = os.path.commonpath([str(candidate.resolve()), str(prepared.build_path.resolve())]) == str(prepared.build_path.resolve())
            except ValueError:
                inside_build = False
            if candidate.is_file() and inside_build:
                return str(candidate.resolve())
        execution = prepared.build_path / "executions" / operation_id
        for candidate in (
            execution / "workspace" / "audisor-artifacts" / "execution-contract.json",
            execution / "evidence" / "aflow-operation-result.json",
        ):
            if candidate.is_file():
                return str(candidate.resolve())
        return None

    def run(self, build_id: str, *, operation_id: str | None = None) -> CodexRunResult | OperationResponse:
        prepared, _prepared_context = self._prepared(build_id)
        context = prepared.instruction.execution_context
        assert context is not None
        selected_operation_id = operation_id or _operation_id(build_id)
        request = self._request(prepared, selected_operation_id)
        if self.operation_service is None:
            from audisor.operations.transport import default_operation_service

            self.operation_service = default_operation_service()
        response = self.operation_service.accept(request)
        if response.status != "accepted" or response.continuation.get("permitted") is not True:
            return response
        contract_reference = self._contract_reference(prepared, selected_operation_id, response)
        if contract_reference is None:
            raise CodexAdapterError("continuation_not_permitted", "Host did not persist a resolvable execution contract")
        if not response.authority_limits and not context.authority_limits:
            raise CodexAdapterError("continuation_not_permitted", "Authority limits are unavailable")
        try:
            self.operation_store.claim_continuation(selected_operation_id, request.canonical_hash())
        except ContinuationClaimError as exc:
            raise CodexAdapterError(getattr(exc, "code", "continuation_claim_failed"), str(exc)) from exc
        handoff = build_handoff(
            operation_id=selected_operation_id,
            build_id=build_id,
            client={"client_id": "codex", "adapter_id": "audisor.codex", "adapter_version": "1.0"},
            prepared=prepared,
            response=response,
        )
        handoff["execution_contract_reference"] = contract_reference
        handoff_root = self.operation_store.root / "codex" / selected_operation_id
        handoff_path, stdin_path, handoff_hash, stdin_hash, stdin_size = persist_handoff(handoff_root, handoff)
        try:
            pid, exit_code, outcome, argv = self.launcher(
                stdin_bytes=stdin_path.read_bytes(),
                cwd=Path(context.target_root).resolve(),
            )
        except CodexLaunchError as exc:
            persist_launch_result(
                handoff_root,
                {"operation_id": selected_operation_id, "outcome": "codex_failed", "failure_code": exc.code, "handoff_sha256": handoff_hash, "stdin_sha256": stdin_hash},
            )
            raise CodexAdapterError(exc.code, str(exc)) from exc
        persist_launch_result(
            handoff_root,
            {"operation_id": selected_operation_id, "outcome": outcome, "pid": pid, "exit_code": exit_code, "handoff_sha256": handoff_hash, "stdin_sha256": stdin_hash},
        )
        return CodexRunResult(
            operation_id=selected_operation_id,
            build_id=build_id,
            response=response,
            handoff_path=handoff_path,
            stdin_path=stdin_path,
            handoff_sha256=handoff_hash,
            stdin_sha256=stdin_hash,
            stdin_size_bytes=stdin_size,
            resolved_command=argv,
            working_directory=Path(context.target_root).resolve(),
            pid=pid,
            exit_code=exit_code,
            outcome=outcome,
        )
