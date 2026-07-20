"""Secure prepared-build execution orchestration for Phase 2B."""

from __future__ import annotations

from pathlib import Path

from audisor.builder.action_parser import (
    normalize_worker_result,
    parse_action_plan,
    sanitized_unusable_output,
)
from audisor.builder.authority import TargetAuthorityResolver
from audisor.builder.evidence import canonical_json_bytes, sanitize_text, sha256_bytes, utc_now
from audisor.builder.execution_store import (
    ExecutionConflictError,
    ExecutionStore,
)
from audisor.builder.global_authority import GlobalAuthorityConflictError
from audisor.builder.idempotency import IdempotencyConflictError, fingerprint_request
from audisor.builder.scheduler import DeterministicScheduler
from audisor.builder.task_loader import LoadedPreparedBuild, PreparedBuildLoader
from audisor.builder.tool_runtime import ToolRuntime, ToolRuntimeError
from audisor.audisor_lifecycle.artifacts import audisor_operation_artifact
from audisor.audisor_lifecycle.ignition import ignite
from audisor.audisor_lifecycle.analysis_package import package_from_context
from audisor.audisor_lifecycle.operation import (
    AudisorOperationContext,
    FrozenAudisorPolicy,
    make_operation_context,
    read_frozen_audisor_policy,
)
from audisor.workers.local import LocalWorker
from audisor.routing.router import ProviderRouter
from audisor.schemas.build import BuildTask
from audisor.schemas.execution import (
    ActionExecutionRecord,
    BuildExecutionRequest,
    BuildExecutionState,
    ChangeRecord,
    CommandEvidence,
    SanitizedWorkerOutput,
    TaskExecutionResult,
    WorkerActionPlan,
)
from audisor.schemas.task_input import TaskInput
from audisor.workers.base import ProviderCapabilityError


class BuildExecutor:
    """Verify, authorize, mutate, statically reconcile, and persist."""

    def __init__(
        self,
        *,
        router: ProviderRouter,
        loader: PreparedBuildLoader,
        authority: TargetAuthorityResolver,
        store: ExecutionStore,
        aflow_policy_reader=read_frozen_audisor_policy,
        aflow_igniter=ignite,
        aflow_worker_factory=LocalWorker,
    ) -> None:
        self.router = router
        self.loader = loader
        self.authority = authority
        self.store = store
        self.aflow_policy_reader = aflow_policy_reader
        self.aflow_igniter = aflow_igniter
        self.aflow_worker_factory = aflow_worker_factory

    @staticmethod
    def _validation_hash(task: BuildTask) -> str:
        payload = [command.model_dump(mode="json") for command in task.validation]
        return sha256_bytes(canonical_json_bytes(payload))

    @staticmethod
    def _execution_prompt(
        prepared: LoadedPreparedBuild,
        task_id: str,
        workspace: Path,
        allowed_paths: tuple[str, ...],
    ) -> str:
        skill = prepared.skills[task_id].content.rstrip()
        resolved_allowed = [
            str((workspace / Path(*path.replace("\\", "/").split("/"))).resolve())
            for path in allowed_paths
        ]
        allowed = "\n".join(f"- {path}" for path in resolved_allowed)
        return "\n".join(
            [
                skill,
                "",
                "## Resolved execution authority",
                f"Isolated workspace root: {workspace.resolve()}",
                "Fully resolved allowed workspace write roots:",
                allowed,
                "Return exactly one JSON mutation-plan object in the answer string.",
                "Use only write_file, create_directory, or delete_file mutations.",
                "Do not return commands, scripts, shells, host paths, or secret values.",
                "Every mutation path must be relative to an allowed workspace root.",
                "Validation is trusted prepared data and is not worker-authored.",
            ]
        )

    def _result(
        self,
        *,
        prepared: LoadedPreparedBuild,
        request: BuildExecutionRequest,
        task: BuildTask,
        status: str,
        worker_input: dict[str, str],
        worker_dispatched: bool,
        worker_output: SanitizedWorkerOutput | None,
        plan: WorkerActionPlan | None,
        actions: list[ActionExecutionRecord],
        commands: list[CommandEvidence],
        changes: list[ChangeRecord],
        expected_outputs_verified: bool,
        executed_validation_sha256: str | None,
        error: object | None,
    ) -> TaskExecutionResult:
        validation_hash = self._validation_hash(task)
        message = None
        if error is not None:
            message, _ = sanitize_text(error, limit=1000)
        return TaskExecutionResult(
            build_id=prepared.plan.build_id,
            execution_id=request.execution_id,
            task_id=task.task_id,
            status=status,
            skill_hash=prepared.skill_hashes[task.task_id],
            plan_hash=prepared.plan_hash,
            worker_input=worker_input,
            worker_dispatched=worker_dispatched,
            worker_output=worker_output,
            requested_actions=plan,
            executed_actions=actions,
            changed_paths=changes,
            validation_commands=commands,
            exit_codes=[item.exit_code for item in commands if item.exit_code is not None],
            prepared_validation_sha256=validation_hash,
            rendered_validation_sha256=validation_hash,
            executed_validation_sha256=executed_validation_sha256,
            expected_outputs_verified=expected_outputs_verified,
            completion_timestamp=utc_now(),
            error=message,
        )

    def _finalize_audisor_failure(self, *, prepared, request, state, claim, global_claim, error) -> BuildExecutionState:
        """Route pre-worker Audisor failure through the existing terminal path."""
        failed_tasks = []
        root_task_ids = {task.task_id for task in prepared.plan.tasks if not task.depends_on}
        for task in prepared.plan.tasks:
            task_status = "failed" if task.task_id in root_task_ids else "blocked"
            result = self._result(
                prepared=prepared,
                request=request,
                task=task,
                status=task_status,
                worker_input={"aflow_failure": type(error).__name__},
                worker_dispatched=False,
                worker_output=None,
                plan=None,
                actions=[],
                commands=[],
                changes=[],
                expected_outputs_verified=False,
                executed_validation_sha256=None,
                error=error,
            )
            self.store.persist_terminal_result(claim.path, result)
            failed_tasks.append(task.task_id)
        failed_state = state.model_copy(
            update={
                "tasks": [item.model_copy(update={"status": "failed" if item.task_id in root_task_ids else "blocked"}) for item in state.tasks],
                "status": "running",
            }
        )
        terminal = self.store.finalize_terminal(
            claim.path,
            failed_state,
            global_claim=global_claim,
            prepared_plan=prepared.plan,
            expected_task_ids=failed_tasks,
        )
        release_evidence = self.store.global_authority.prepare_release_evidence(
            global_claim, terminal_status=terminal.status
        )
        self.store.global_authority.release(
            global_claim,
            terminal_status=terminal.status,
            terminal_manifest_sha256=terminal.terminal_manifest_sha256 or "",
            release_evidence_sha256=release_evidence.sha256,
            reconciliation_verified=True,
        )
        self.store.workspace_manager.cleanup(claim.path / "workspace")
        return self.store.final_state(
            claim.path,
            prepared_plan=prepared.plan,
            expected_task_ids=[task.task_id for task in prepared.plan.tasks],
        )

    def execute(self, build_id: str, request: BuildExecutionRequest) -> BuildExecutionState:
        request_payload = {"build_id": build_id, **request.model_dump(mode="json")}
        request_fingerprint = fingerprint_request(request_payload)
        try:
            existing = self.store.idempotency.lookup_before_resolution(
                request.idempotency_key, request_payload
            )
        except IdempotencyConflictError as exc:
            raise ExecutionConflictError(str(exc)) from exc
        if existing is not None:
            return self.store.final_state(Path(existing.execution_path))

        predicted_path = (
            self.loader.store.build_path(build_id)
            / "executions"
            / request.execution_id
        ).resolve()
        prepared = self.loader.load(build_id)
        provider = self.router.select_provider()
        if not provider.capabilities().text:
            raise ProviderCapabilityError(
                "Selected provider does not support text tasks",
                internal_detail="required=text",
            )
        resolved = self.authority.resolve(
            build_id,
            request,
            plan_hash=prepared.plan_hash,
            integrity_root=prepared.integrity_root,
            selected_provider=provider.provider_id,
            workspace_path=predicted_path / "workspace",
        )
        try:
            global_claim = self.store.global_authority.acquire(
                build_id=build_id,
                execution_id=request.execution_id,
                idempotency_key=request.idempotency_key,
                request_fingerprint=request_fingerprint,
                target_root=resolved.resolved_target,
                allowed_paths=resolved.record.allowed_resolved_paths,
            )
        except GlobalAuthorityConflictError as exc:
            raise ExecutionConflictError(str(exc)) from exc

        scheduler = DeterministicScheduler(prepared.plan, request.execution_id)
        initial = scheduler.initial_state()
        claim = self.store.claim(
            build_path=prepared.build_path,
            request=request,
            authority=resolved.record,
            baseline=resolved.baseline,
            prepared_plan=prepared.plan,
            initial_state=initial,
            target_root=resolved.resolved_target,
        )
        with claim:
            try:
                binding = self.store.idempotency.bind(
                    idempotency_key=request.idempotency_key,
                    request_fingerprint=request_fingerprint,
                    build_id=build_id,
                    execution_id=request.execution_id,
                    execution_path=claim.path,
                )
            except IdempotencyConflictError as exc:
                raise ExecutionConflictError(str(exc)) from exc
            if not claim.is_new:
                terminal = self.store.final_state(
                    claim.path,
                    prepared_plan=prepared.plan,
                    expected_task_ids=[task.task_id for task in prepared.plan.tasks],
                )
                raise ExecutionConflictError(
                    "Existing execution requires explicit authority recovery; "
                    f"durable status is {terminal.status}"
                )

            state = claim.state
            workspace = claim.path / "workspace"
            policy: FrozenAudisorPolicy = self.aflow_policy_reader()
            accepted_task = {"id": prepared.instruction.build_id, **prepared.instruction.model_dump(mode="json")}
            accepted_plan = prepared.plan.model_dump(mode="json")
            authority_context = resolved.record.model_dump(mode="json")
            repository_context = {
                "authority": authority_context,
                "baseline_evidence": resolved.baseline.model_dump(mode="json"),
                "accepted_constraints": {"build_id": build_id, "execution_id": request.execution_id},
                "required_outputs": sorted({path for task in prepared.plan.tasks for path in task.expected_outputs}),
                "success_definition": prepared.instruction.execution_context.success_definition if prepared.instruction.execution_context else {},
                "validation_requirements": prepared.instruction.execution_context.validation_requirements if prepared.instruction.execution_context else [],
                "build_tasks": [task.model_dump(mode="json") for task in prepared.plan.tasks],
            }
            if request.aflow_analysis_request is not None:
                repository_context["aflow_analysis_request"] = request.aflow_analysis_request
            workspace_identity = {"path": str(workspace.resolve()), "build_id": build_id}
            analysis_package = None
            if policy.enabled and self.aflow_igniter is ignite:
                try:
                    analysis_package = package_from_context(
                        operation_id=request.execution_id,
                        operation_type="build",
                        accepted_task=accepted_task,
                        accepted_plan=accepted_plan,
                        authority_context=authority_context,
                        repository_context=repository_context,
                        workspace_identity=workspace_identity,
                        provider_policy={
                            "provider": policy.provider,
                            "base_url": policy.base_url,
                            "model_id": policy.model_id,
                            "timeout_seconds": policy.timeout_seconds,
                        },
                    )
                except Exception as exc:
                    operation_context = make_operation_context(
                        operation_id=request.execution_id,
                        operation_type="build",
                        accepted_task=accepted_task,
                        accepted_plan=accepted_plan,
                        repository_context=repository_context,
                        workspace_identity=workspace_identity,
                        authority_context=authority_context,
                    )
                    failure = audisor_operation_artifact(
                        operation_context,
                        policy,
                        status="package_validation_failed",
                        error=exc,
                    )
                    self.store.persist_audisor_result(claim.path, failure)
                    return self._finalize_audisor_failure(
                        prepared=prepared,
                        request=request,
                        state=state,
                        claim=claim,
                        global_claim=global_claim,
                        error=exc,
                    )
            operation_context: AudisorOperationContext = make_operation_context(
                operation_id=request.execution_id,
                operation_type="build",
                accepted_task=accepted_task,
                accepted_plan=accepted_plan,
                repository_context=repository_context,
                workspace_identity=workspace_identity,
                authority_context=authority_context,
                analysis_package=analysis_package,
            )
            if not policy.enabled:
                skipped = audisor_operation_artifact(operation_context, policy, status="skipped_disabled")
                self.store.persist_audisor_result(claim.path, skipped)
            else:
                worker = self.aflow_worker_factory(
                    policy.base_url,
                    policy.model_id,
                    timeout_seconds=policy.timeout_seconds,
                )
                try:
                    audisor_result = self.aflow_igniter(
                        operation_context=operation_context,
                        policy=policy,
                        worker=worker,
                    )
                except Exception as exc:
                    failure = audisor_operation_artifact(
                        operation_context,
                        policy,
                        status="provider_failed" if getattr(exc, "code", "").startswith("provider") else "validation_failed",
                        error=exc,
                    )
                    self.store.persist_audisor_result(claim.path, failure)
                    return self._finalize_audisor_failure(
                        prepared=prepared,
                        request=request,
                        state=state,
                        claim=claim,
                        global_claim=global_claim,
                        error=exc,
                    )
                # Build Audisor is analysis-only: a valid result enriches the
                # original plan and never becomes an approval or execution
                # contract.  The host remains responsible for the next step.
                if audisor_result.build_analysis is not None:
                    status = "analysis_completed"
                else:
                    status = "accepted" if audisor_result.implementation_eligible else "rejected"
                artifact = audisor_operation_artifact(operation_context, policy, status=status, result=audisor_result)
                self.store.persist_audisor_result(claim.path, artifact)
                if audisor_result.build_analysis is None and not audisor_result.implementation_eligible:
                    return self._finalize_audisor_failure(
                        prepared=prepared,
                        request=request,
                        state=state,
                        claim=claim,
                        global_claim=global_claim,
                        error=ExecutionConflictError("Audisor rejected the operation"),
                    )
            while state.status == "running":
                task = scheduler.next_ready(state)
                if task is None:
                    break
                state = scheduler.mark_running(state, task.task_id)
                self.store.persist_state(claim.path, state)
                prompt = self._execution_prompt(
                    prepared,
                    task.task_id,
                    workspace,
                    resolved.allowed_relative_paths,
                )
                task_input = TaskInput(task_id=task.task_id, prompt=prompt)
                worker_input = task_input.model_dump(mode="json")
                self.store.persist_worker_input(claim.path, task.task_id, worker_input)

                raw_output: object | None = None
                sanitized_output: SanitizedWorkerOutput | None = None
                mutation_plan: WorkerActionPlan | None = None
                actions: list[ActionExecutionRecord] = []
                commands: list[CommandEvidence] = []
                changes: list[ChangeRecord] = []
                expected_verified = False
                executed_validation_hash: str | None = None
                try:
                    raw_output = provider.execute(task_input)
                    normalized = normalize_worker_result(raw_output, task.task_id)
                    mutation_plan, sanitized_output = parse_action_plan(normalized)
                    expected = {path.replace("\\", "/").casefold() for path in task.expected_outputs}
                    planned = {
                        path.replace("\\", "/").casefold()
                        for path in mutation_plan.expected_changed_paths
                    }
                    if expected != planned:
                        raise ToolRuntimeError(
                            "Worker changed paths do not match prepared expected outputs"
                        )
                    self.store.persist_worker_output(
                        claim.path,
                        task.task_id,
                        sanitized_output.model_dump(mode="json"),
                    )
                    self.store.persist_action_progress(
                        claim.path, task.task_id, mutation_plan, actions, commands
                    )
                    runtime = ToolRuntime(workspace, resolved.allowed_relative_paths)

                    def progress(
                        current_actions: list[ActionExecutionRecord],
                        current_commands: list[CommandEvidence],
                    ) -> None:
                        self.store.persist_action_progress(
                            claim.path,
                            task.task_id,
                            mutation_plan,
                            current_actions,
                            current_commands,
                        )

                    actions, _, changes = runtime.execute(mutation_plan, progress)
                    runtime.verify_expected_outputs(task.expected_outputs)
                    expected_verified = True
                    if not self.authority.target_matches_baseline(
                        resolved.resolved_target, resolved.baseline
                    ):
                        raise ToolRuntimeError(
                            "The real target changed during isolated execution",
                            actions=actions,
                            commands=commands,
                            changes=changes,
                        )
                    result = self._result(
                        prepared=prepared,
                        request=request,
                        task=task,
                        status="completed",
                        worker_input=worker_input,
                        worker_dispatched=True,
                        worker_output=sanitized_output,
                        plan=mutation_plan,
                        actions=actions,
                        commands=commands,
                        changes=changes,
                        expected_outputs_verified=expected_verified,
                        executed_validation_sha256=executed_validation_hash,
                        error=None,
                    )
                    self.store.persist_terminal_result(claim.path, result)
                    state = scheduler.mark_completed(state, task.task_id)
                    self.store.persist_state(claim.path, state)
                except Exception as exc:
                    if isinstance(exc, ToolRuntimeError):
                        actions = exc.actions or actions
                        commands = exc.commands or commands
                        changes = exc.changes or changes
                    if sanitized_output is None:
                        sanitized_output = sanitized_unusable_output(raw_output, task.task_id)
                    self.store.persist_worker_output(
                        claim.path,
                        task.task_id,
                        sanitized_output.model_dump(mode="json")
                        if sanitized_output is not None
                        else {"unavailable": True},
                    )
                    result = self._result(
                        prepared=prepared,
                        request=request,
                        task=task,
                        status="failed",
                        worker_input=worker_input,
                        worker_dispatched=True,
                        worker_output=sanitized_output,
                        plan=mutation_plan,
                        actions=actions,
                        commands=commands,
                        changes=changes,
                        expected_outputs_verified=expected_verified,
                        executed_validation_sha256=executed_validation_hash,
                        error=exc,
                    )
                    self.store.persist_terminal_result(claim.path, result)
                    state = scheduler.mark_failed(state, task.task_id)
                    self.store.persist_state(claim.path, state)

            task_by_id = {task.task_id: task for task in prepared.plan.tasks}
            for task_state in state.tasks:
                if task_state.status != "blocked":
                    continue
                result_path = claim.path / "results" / f"{task_state.task_id}.json"
                if result_path.exists():
                    continue
                blocked_task = task_by_id[task_state.task_id]
                blocked_input = TaskInput(
                    task_id=blocked_task.task_id,
                    prompt=self._execution_prompt(
                        prepared,
                        blocked_task.task_id,
                        workspace,
                        resolved.allowed_relative_paths,
                    ),
                ).model_dump(mode="json")
                blocked_result = self._result(
                    prepared=prepared,
                    request=request,
                    task=blocked_task,
                    status="blocked",
                    worker_input=blocked_input,
                    worker_dispatched=False,
                    worker_output=None,
                    plan=None,
                    actions=[],
                    commands=[],
                    changes=[],
                    expected_outputs_verified=False,
                    executed_validation_sha256=None,
                    error="Blocked by a failed dependency",
                )
                self.store.persist_terminal_result(claim.path, blocked_result)

            terminal = self.store.finalize_terminal(
                claim.path,
                state,
                global_claim=global_claim,
                prepared_plan=prepared.plan,
                expected_task_ids=[task.task_id for task in prepared.plan.tasks],
            )
            if terminal.status not in {"completed", "failed"}:
                raise ExecutionConflictError("Terminal evidence is not valid")
            release_evidence = self.store.global_authority.prepare_release_evidence(
                global_claim,
                terminal_status=terminal.status,
            )
            self.store.global_authority.release(
                global_claim,
                terminal_status=terminal.status,
                terminal_manifest_sha256=terminal.terminal_manifest_sha256 or "",
                release_evidence_sha256=release_evidence.sha256,
                reconciliation_verified=True,
            )
            terminal = self.store.final_state(
                claim.path,
                prepared_plan=prepared.plan,
                expected_task_ids=[task.task_id for task in prepared.plan.tasks],
            )
            if terminal.status not in {"completed", "failed"}:
                raise ExecutionConflictError(
                    "Released terminal authority evidence did not reconcile"
                )
            return terminal
