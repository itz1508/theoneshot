"""Secure prepared-build execution orchestration for Phase 2B."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from audisor.builder.action_parser import (
    normalize_worker_result,
    parse_action_plan,
    sanitized_unusable_output,
)
from audisor.builder.authority import TargetAuthorityResolver
from audisor.builder.evidence import (
    atomic_write_json,
    canonical_json_bytes,
    sanitize_text,
    sha256_bytes,
    utc_now,
)
from audisor.builder.execution_store import (
    ExecutionConflictError,
    ExecutionStore,
    FileLock,
)
from audisor.builder.global_authority import (
    AuthorityClaim,
    GlobalAuthorityConflictError,
    derive_authority_key,
)
from audisor.builder.idempotency import IdempotencyConflictError, fingerprint_request
from audisor.builder.operation_envelope import OperationEnvelope, is_routing_enabled
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
from audisor.schemas.build import BuildPlan, BuildTask
from audisor.schemas.execution import (
    ActionExecutionRecord,
    BuildExecutionRequest,
    BuildExecutionState,
    ChangeRecord,
    CommandEvidence,
    SanitizedWorkerOutput,
    TargetAuthorityRecord,
    TargetBaseline,
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

    def _suspend_for_routing(
        self,
        *,
        prepared,
        request,
        state,
        claim,
        global_claim,
        reason: str,
        artifact: dict | None = None,
    ) -> BuildExecutionState:
        """Suspend the operation instead of terminating it.

        Preserves workspace and global claim (as suspended hold with TTL).
        Returns a state with status='suspended'. The operation can be resumed
        via resume_execution() with appropriate resume input.
        """
        envelope = OperationEnvelope()
        envelope.suspend(
            operation_id=request.execution_id,
            reason=reason,
            state_path=claim.path,
            claim=global_claim.record.claim_id if global_claim else None,
            artifact=artifact or {},
        )

        # Update state to suspended without cleaning up workspace or marking tasks failed
        suspended_state = state.model_copy(update={"status": "suspended"})
        self.store.persist_state(claim.path, suspended_state)

        return suspended_state

    def resume_execution(
        self,
        operation_id: str,
        revised_plan: dict | None = None,
        resume_input: dict | None = None,
    ) -> BuildExecutionState:
        """Resume a suspended operation from its preserved execution artifacts.

        Validates resume input against the suspension schema, reconstructs the
        persisted authority and still-held global claim, and re-enters the task
        loop. A revised plan replaces the locked prepared plan on disk so the
        executed plan and the persisted plan remain identical at terminal
        reconciliation.
        """
        envelope = OperationEnvelope()
        execution_path = envelope.resume(operation_id, resume_input or {})
        if not execution_path.is_dir():
            raise ExecutionConflictError("Preserved execution state is missing")

        state = self.store.load_state(execution_path)
        if state.status != "suspended":
            raise ExecutionConflictError("Only a suspended execution can be resumed")

        request = BuildExecutionRequest.model_validate(
            json.loads((execution_path / "request.json").read_text(encoding="utf-8"))
        )
        authority = TargetAuthorityRecord.model_validate(
            json.loads((execution_path / "authority.json").read_text(encoding="utf-8"))
        )
        baseline = TargetBaseline.model_validate(
            json.loads((execution_path / "baseline.json").read_text(encoding="utf-8"))
        )
        if request.execution_id != operation_id:
            raise ExecutionConflictError("Preserved execution identity does not match")

        prepared = self.loader.load(state.build_id)
        provider = self.router.select_provider()
        if not provider.capabilities().text:
            raise ProviderCapabilityError(
                "Selected provider does not support text tasks",
                internal_detail="required=text",
            )

        # Suspension retains the global claim as a hold; reconstruct it from
        # the persisted authority scope instead of acquiring a new one.
        authority_key = derive_authority_key(
            authority.resolved_target_root, authority.allowed_resolved_paths
        )
        active = self.store.global_authority.load_active(authority_key)
        if active is None or active.execution_id != operation_id:
            raise ExecutionConflictError(
                "Suspended execution no longer holds its global authority"
            )
        global_claim = AuthorityClaim(
            path=self.store.global_authority.active_root / f"{authority_key}.json",
            record=active,
        )

        if revised_plan is not None:
            plan = BuildPlan.model_validate(revised_plan)
            if plan.build_id != prepared.plan.build_id:
                raise ExecutionConflictError("Revised plan targets a different build")
            missing = [
                task.task_id
                for task in plan.tasks
                if task.task_id not in prepared.skills
            ]
            if missing:
                raise ExecutionConflictError(
                    "Revised plan tasks have no prepared skills"
                )
            # The revised plan becomes the locked prepared plan for this
            # execution so terminal reconciliation proves executed == locked.
            atomic_write_json(
                execution_path / "prepared-plan.json", plan.model_dump(mode="json")
            )
            prepared = replace(
                prepared,
                plan=plan,
                plan_hash=sha256_bytes(
                    canonical_json_bytes(plan.model_dump(mode="json"))
                ),
            )

        scheduler = DeterministicScheduler(prepared.plan, operation_id)
        lock = FileLock(execution_path.parent / f".{execution_path.name}.execution.lock")
        if not lock.acquire(blocking=False):
            raise ExecutionConflictError("Execution lock is unavailable for resume")
        try:
            # Every suspension point fires before the first task dispatch, so
            # resume re-enters the loop from a fresh dependency-ready state.
            state = scheduler.initial_state()
            self.store.persist_state(execution_path, state)
            return self._run_to_terminal(
                prepared=prepared,
                request=request,
                provider=provider,
                scheduler=scheduler,
                state=state,
                execution_path=execution_path,
                workspace=execution_path / "workspace",
                allowed_relative_paths=tuple(request.allowed_write_paths),
                resolved_target=Path(authority.resolved_target_root),
                baseline=baseline,
                global_claim=global_claim,
            )
        finally:
            lock.release()

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
                    if is_routing_enabled():
                        return self._suspend_for_routing(
                            prepared=prepared,
                            request=request,
                            state=state,
                            claim=claim,
                            global_claim=global_claim,
                            reason="needs_package_repair",
                            artifact={"error": str(exc), "stage": "package_validation"},
                        )
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
                    if is_routing_enabled():
                        reason = "needs_provider_recovery" if getattr(exc, "code", "").startswith("provider") else "needs_evidence"
                        return self._suspend_for_routing(
                            prepared=prepared,
                            request=request,
                            state=state,
                            claim=claim,
                            global_claim=global_claim,
                            reason=reason,
                            artifact={"error": str(exc), "stage": "aflow_ignition"},
                        )
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
                    # Phase 2.1: When material gap found, suspend for plan revision
                    # instead of falling through to the scheduler with the original plan.
                    if is_routing_enabled() and hasattr(audisor_result.build_analysis, 'gap_evaluation'):
                        gap_eval = audisor_result.build_analysis.gap_evaluation
                        if hasattr(gap_eval, 'result') and gap_eval.result == "material_gap_found":
                            artifact = audisor_operation_artifact(operation_context, policy, status=status, result=audisor_result)
                            self.store.persist_audisor_result(claim.path, artifact)
                            updated_plan = getattr(audisor_result.build_analysis, 'updated_original_plan', None)
                            return self._suspend_for_routing(
                                prepared=prepared,
                                request=request,
                                state=state,
                                claim=claim,
                                global_claim=global_claim,
                                reason="needs_plan_revision",
                                artifact={
                                    "stage": "build_analysis",
                                    "updated_original_plan": updated_plan,
                                    "gap_evaluation": str(gap_eval),
                                },
                            )
                else:
                    status = "accepted" if audisor_result.implementation_eligible else "rejected"
                artifact = audisor_operation_artifact(operation_context, policy, status=status, result=audisor_result)
                self.store.persist_audisor_result(claim.path, artifact)
                if audisor_result.build_analysis is None and not audisor_result.implementation_eligible:
                    if is_routing_enabled():
                        return self._suspend_for_routing(
                            prepared=prepared,
                            request=request,
                            state=state,
                            claim=claim,
                            global_claim=global_claim,
                            reason="needs_evidence",
                            artifact={"stage": "audisor_rejection"},
                        )
                    return self._finalize_audisor_failure(
                        prepared=prepared,
                        request=request,
                        state=state,
                        claim=claim,
                        global_claim=global_claim,
                        error=ExecutionConflictError("Audisor rejected the operation"),
                    )
            return self._run_to_terminal(
                prepared=prepared,
                request=request,
                provider=provider,
                scheduler=scheduler,
                state=state,
                execution_path=claim.path,
                workspace=workspace,
                allowed_relative_paths=resolved.allowed_relative_paths,
                resolved_target=resolved.resolved_target,
                baseline=resolved.baseline,
                global_claim=global_claim,
            )

    def _run_to_terminal(
        self,
        *,
        prepared: LoadedPreparedBuild,
        request: BuildExecutionRequest,
        provider,
        scheduler: DeterministicScheduler,
        state: BuildExecutionState,
        execution_path: Path,
        workspace: Path,
        allowed_relative_paths: tuple[str, ...],
        resolved_target: Path,
        baseline: TargetBaseline,
        global_claim: AuthorityClaim,
    ) -> BuildExecutionState:
        """Dispatch ready tasks, then anchor, release, and reconcile terminally."""
        while state.status == "running":
            task = scheduler.next_ready(state)
            if task is None:
                break
            state = scheduler.mark_running(state, task.task_id)
            self.store.persist_state(execution_path, state)
            prompt = self._execution_prompt(
                prepared,
                task.task_id,
                workspace,
                allowed_relative_paths,
            )
            task_input = TaskInput(task_id=task.task_id, prompt=prompt)
            worker_input = task_input.model_dump(mode="json")
            self.store.persist_worker_input(execution_path, task.task_id, worker_input)

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
                    execution_path,
                    task.task_id,
                    sanitized_output.model_dump(mode="json"),
                )
                self.store.persist_action_progress(
                    execution_path, task.task_id, mutation_plan, actions, commands
                )
                runtime = ToolRuntime(workspace, allowed_relative_paths)

                def progress(
                    current_actions: list[ActionExecutionRecord],
                    current_commands: list[CommandEvidence],
                ) -> None:
                    self.store.persist_action_progress(
                        execution_path,
                        task.task_id,
                        mutation_plan,
                        current_actions,
                        current_commands,
                    )

                actions, _, changes = runtime.execute(mutation_plan, progress)
                runtime.verify_expected_outputs(task.expected_outputs)
                expected_verified = True
                if not self.authority.target_matches_baseline(
                    resolved_target, baseline
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
                self.store.persist_terminal_result(execution_path, result)
                state = scheduler.mark_completed(state, task.task_id)
                self.store.persist_state(execution_path, state)
            except Exception as exc:
                if isinstance(exc, ToolRuntimeError):
                    actions = exc.actions or actions
                    commands = exc.commands or commands
                    changes = exc.changes or changes
                if sanitized_output is None:
                    sanitized_output = sanitized_unusable_output(raw_output, task.task_id)
                self.store.persist_worker_output(
                    execution_path,
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
                self.store.persist_terminal_result(execution_path, result)
                state = scheduler.mark_failed(state, task.task_id)
                self.store.persist_state(execution_path, state)

        task_by_id = {task.task_id: task for task in prepared.plan.tasks}
        for task_state in state.tasks:
            if task_state.status != "blocked":
                continue
            result_path = execution_path / "results" / f"{task_state.task_id}.json"
            if result_path.exists():
                continue
            blocked_task = task_by_id[task_state.task_id]
            blocked_input = TaskInput(
                task_id=blocked_task.task_id,
                prompt=self._execution_prompt(
                    prepared,
                    blocked_task.task_id,
                    workspace,
                    allowed_relative_paths,
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
            self.store.persist_terminal_result(execution_path, blocked_result)

        terminal = self.store.finalize_terminal(
            execution_path,
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
            execution_path,
            prepared_plan=prepared.plan,
            expected_task_ids=[task.task_id for task in prepared.plan.tasks],
        )
        if terminal.status not in {"completed", "failed"}:
            raise ExecutionConflictError(
                "Released terminal authority evidence did not reconcile"
            )
        return terminal
