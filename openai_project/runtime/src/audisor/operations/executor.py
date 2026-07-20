"""Canonical Audisor operation executor.

The host-agnostic execution core that all adapters (Codex, MCP, CLI,
Responses-compatible) converge on.  Owns orchestration, authority freeze,
idempotency, and result state.  Contains NO host-specific handling.

Architecture:
  Host Request → Adapter → AudisorOperationRequest
                                    ↓
                         AudisorOperationExecutor.execute()
                                    ↓
                         AudisorOperationResult
                                    ↓
                         Adapter → Host Response
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal, Mapping, Protocol

from audisor.adapters.protocol import (
    AudisorOperationRequest,
    HostCapabilities,
)
from audisor.config.host_profiles import AudisorConfig, HostProfile
from audisor.policies.model_failure import DEFAULT_MODEL_FAILURE_POLICY, ModelFailurePolicy
from audisor.schemas.authority import AuthorityContext, CanonicalAuthority
from audisor.schemas.errors import AudisorRuntimeError
from audisor.schemas.idempotency import IdempotencyContext, IdempotencyKey, IdempotencyStore
from audisor.operations.context import AudisorOperationContext
from audisor.schemas.operation import (
    OperationConstraints,
    OperationEvidence,
)
from audisor.schemas.task_input import TaskInput
from audisor.schemas.task_output import TaskOutput
from audisor.workers.local import LocalWorker

from .artifacts import ArtifactManifest, ArtifactStore
from .mutation_enforcer import MutationEnforcer, MutationReceipt
from .result import AudisorOperationResult
from .store import AudisorOperationStore


# Type alias for the execution worker function
WorkerFactory = Callable[[AudisorConfig | None], LocalWorker]


class FixDispatcher(Protocol):
    """Protocol for routing Fix operations to the backend Fix dispatcher.

    Defined here so the canonical runtime does not import audisor_backend
    at module load time.  The transport layer provides a lazy concrete
    implementation that imports audisor_backend only when a Fix request
    is actually dispatched.
    """

    def dispatch(
        self,
        operation: Any,
        continue_implementation: Callable[..., Any],
        finalize_unresolved: Callable[..., Any],
    ) -> Any: ...


@dataclass
class FixRouteConfig:
    """Configuration for routing Fix operations to the backend dispatcher.

    When present on ExecutorConfig, Fix operations are dispatched through
    fix_dispatcher instead of the generic LocalWorker mutation path.

    If fix_continuation is set, an accepted Fix automatically launches
    Codex under the same operation ID (automatic host continuation).
    """

    fix_dispatcher: FixDispatcher
    continue_callback: Callable[..., Any]
    finalize_callback: Callable[..., Any]
    fix_continuation: Any | None = None


@dataclass
class ExecutorConfig:
    """Configuration for the AudisorOperationExecutor."""

    operation_store: AudisorOperationStore
    artifact_store: ArtifactStore
    mutation_enforcer: MutationEnforcer
    model_failure_policy: ModelFailurePolicy = field(default_factory=lambda: DEFAULT_MODEL_FAILURE_POLICY)
    worker_factory: WorkerFactory | None = None
    fix_route: FixRouteConfig | None = None
    aflow_enabled: bool = True


class AudisorOperationExecutor:
    """Canonical execution core for all Audisor operations.

    Host adapters produce AudisorOperationRequest; this executor consumes it
    and produces AudisorOperationResult.  No adapter-specific logic lives here.
    """

    def __init__(self, config: ExecutorConfig) -> None:
        self._store = config.operation_store
        self._artifacts = config.artifact_store
        self._enforcer = config.mutation_enforcer
        self._policy = config.model_failure_policy
        self._worker_factory = config.worker_factory
        self._fix_route = config.fix_route
        self._aflow_enabled = config.aflow_enabled

    # ------------------------------------------------------------------
    # Public API: single entry point for all operations
    # ------------------------------------------------------------------

    def execute(
        self,
        request: AudisorOperationRequest,
        *,
        audisor_config: AudisorConfig | None = None,
    ) -> AudisorOperationResult:
        """Execute an Audisor operation from a canonical request.

        This is the ONLY entry point for operation execution.  All host
        adapters must call this method; no adapter may bypass it.
        """
        # 1. Build canonical operation context
        try:
            context = self._build_context(request)
        except Exception as exc:
            return AudisorOperationResult.from_error(
                request.operation_id,
                AudisorRuntimeError(
                    category="validation",
                    stage="request_translation",
                    code="context_build_failed",
                    message=f"Failed to build operation context: {exc}",
                ),
                status="blocked",
            )

        # 2. Idempotency check
        idem_key = self._build_idempotency_key(request)
        store_status, store_state = self._store.create(
            context.operation_id,
            context.to_mapping(),
            idempotency_key=idem_key,
        )

        if store_status == "conflict":
            return AudisorOperationResult.from_error(
                context.operation_id,
                AudisorRuntimeError(
                    category="contract",
                    stage="idempotency_check",
                    code="idempotency_conflict",
                    message="Operation conflicts with an existing operation with different parameters",
                ),
                status="blocked",
            )

        if store_status == "existing" and store_state is not None:
            # Return cached result
            return self._reconstruct_result(store_state)

        # 3. Start execution
        self._store.start(context.operation_id)

        # 4. Authority enforcement (freeze authority)
        try:
            receipt = self._enforce_authority(context)
        except AudisorRuntimeError as exc:
            self._store.block(context.operation_id, f"{exc.code}: {exc.message}", idempotency_key=idem_key)
            return AudisorOperationResult.from_error(context.operation_id, exc, status="blocked")

        # 5. Dispatch by mode
        try:
            if context.mode == "fix":
                # Fix must never enter the generic LocalWorker mutation path.
                # When no fix_route is configured, block immediately with a
                # configuration/contract error — do not construct a worker,
                # do not call _execute_mutation, do not persist a
                # mutation-completed result.
                if self._fix_route is None:
                    raise AudisorRuntimeError(
                        category="configuration",
                        stage="request_translation",
                        code="fix_route_unavailable",
                        message="Fix operation received but no Fix route is configured",
                    )
                result = self._execute_fix(context, receipt)
            elif context.mode == "build":
                result = self._execute_mutation(context, receipt, audisor_config=audisor_config)
            elif context.mode in ("analyze", "validate"):
                result = self._execute_read_only(context, receipt, audisor_config=audisor_config)
            else:
                raise AudisorRuntimeError(
                    category="validation",
                    stage="request_translation",
                    code="unsupported_mode",
                    message=f"Unsupported operation mode: {context.mode}",
                )
        except AudisorRuntimeError as exc:
            # Validation, contract, and configuration errors block the
            # operation; provider/internal/network errors fail it.
            if exc.category in ("validation", "contract", "configuration"):
                self._store.block(context.operation_id, f"{exc.code}: {exc.message}", idempotency_key=idem_key)
                return AudisorOperationResult.from_error(context.operation_id, exc, status="blocked")
            self._store.fail(context.operation_id, exc.code, exc.message, idempotency_key=idem_key)
            return AudisorOperationResult.from_error(context.operation_id, exc)
        except Exception as exc:
            error = AudisorRuntimeError(
                category="internal",
                stage="execution",
                code="unexpected_error",
                message=f"Unexpected error during execution: {exc}",
                detail=type(exc).__name__,
            )
            self._store.fail(context.operation_id, error.code, error.message, idempotency_key=idem_key)
            return AudisorOperationResult.from_error(context.operation_id, error)

        # 6. Persist and return
        self._store.complete(
            context.operation_id,
            result.to_mapping(),
            artifacts=list(result.artifacts) if result.artifacts else None,
            idempotency_key=idem_key,
        )
        return result

    # ------------------------------------------------------------------
    # Internal: context building
    # ------------------------------------------------------------------

    def _build_context(self, request: AudisorOperationRequest) -> AudisorOperationContext:
        """Convert adapter request to canonical operation context."""
        authority = CanonicalAuthority.from_context(request.authority)

        # Build constraints from host capabilities
        constraints = OperationConstraints(
            max_tokens=request.host_capabilities.max_response_size_bytes // 1000,
            timeout_seconds=300.0,
            allowed_modes=("build", "fix", "analyze", "validate"),
            require_authority_confirmation=True,
            require_idempotency=True,
        )

        # Validate mode
        if request.mode not in constraints.allowed_modes:
            raise ValueError(f"Mode '{request.mode}' not in allowed modes: {constraints.allowed_modes}")

        return AudisorOperationContext(
            operation_id=request.operation_id,
            mode=request.mode,
            request=dict(request.request),
            authority=authority,
            constraints=constraints,
            idempotency=request.idempotency,
            host_identity=request.authority.source.host_identity or "unknown",
            host_capabilities=request.host_capabilities.to_mapping(),
        )

    def _build_idempotency_key(
        self,
        request: AudisorOperationRequest,
    ) -> IdempotencyKey | None:
        """Build an idempotency key from the request, or None if not provided."""
        if request.idempotency is not None:
            return IdempotencyKey(
                key=request.idempotency.key,
                scope=request.idempotency.scope,
            )
        # Auto-generate from request content hash
        payload = json.dumps(request.request, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        key = f"auto:{request.operation_id}:{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:16]}"
        return IdempotencyKey(key=key, scope="operation")

    # ------------------------------------------------------------------
    # Internal: authority enforcement
    # ------------------------------------------------------------------

    def _enforce_authority(self, context: AudisorOperationContext) -> MutationReceipt:
        """Freeze authority and return a mutation receipt.

        For read-only operations, this validates paths only.
        For mutation operations, this validates paths and tools.
        """
        if context.mode in ("build", "fix"):
            # Mutation: full authorization required
            # Extract target paths from request if present
            target_paths = self._extract_target_paths(context.request)
            return self._enforcer.authorize_mutation(
                context.operation_id,
                context.authority,
                target_paths=target_paths,
            )
        else:
            # Read-only: path validation only
            paths = self._extract_target_paths(context.request) or []
            return self._enforcer.check_read_only(
                context.operation_id,
                context.authority,
                paths,
            )

    def _extract_target_paths(self, request: Mapping[str, Any]) -> list[str] | None:
        """Extract target paths from a request payload."""
        paths: list[str] = []

        def _add(value: Any) -> None:
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, str):
                        paths.append(item)
                    elif isinstance(item, dict) and "path" in item:
                        paths.append(str(item["path"]))
            elif isinstance(value, str):
                paths.append(value)

        # Common path fields in requests
        for key in ("paths", "target_paths", "files", "target_files"):
            _add(request.get(key))

        # Authority / scope paths
        requested_scope = request.get("requested_scope")
        if isinstance(requested_scope, dict):
            for key in ("paths", "allowed_paths"):
                _add(requested_scope.get(key))

        # Build request paths
        build = request.get("build")
        if isinstance(build, dict):
            build_request = build.get("request")
            if isinstance(build_request, dict):
                _add(build_request.get("target_root"))
                _add(build_request.get("allowed_write_paths"))
                _add(build_request.get("target_paths"))

        # Check nested plan structures
        if "plan" in request and isinstance(request["plan"], dict):
            plan = request["plan"]
            for key in ("target_paths", "files", "steps"):
                _add(plan.get(key))

        return paths if paths else None

    # ------------------------------------------------------------------
    # Internal: Fix dispatch
    # ------------------------------------------------------------------

    def _execute_fix(
        self,
        context: AudisorOperationContext,
        receipt: MutationReceipt,
    ) -> AudisorOperationResult:
        """Execute a Fix operation through the backend Fix dispatcher.

        Reconstructs the typed Fix package from the serialized request,
        invokes the AcceptedFixDispatcher, and translates the result into
        an AudisorOperationResult.  The generic LocalWorker is NOT used.
        """
        assert self._fix_route is not None
        fix_package = context.request.get("fix")
        if not fix_package:
            raise AudisorRuntimeError(
                category="validation",
                stage="request_translation",
                code="fix_payload_missing",
                message="Fix mode request does not contain a fix payload",
            )
        operation = self._reconstruct_fix_operation(fix_package)

        # Wrap callbacks to capture which path the dispatcher took.
        dispatched_result: dict[str, Any] = {"value": None, "path": None}

        def _continue(op: Any, result: Any) -> Any:
            dispatched_result["path"] = "continue"
            dispatched_result["value"] = self._fix_route.continue_callback(op, result)  # type: ignore[misc]
            return dispatched_result["value"]

        def _finalize(op: Any, result: Any) -> Any:
            dispatched_result["path"] = "finalize"
            dispatched_result["value"] = self._fix_route.finalize_callback(op, result)  # type: ignore[misc]
            return dispatched_result["value"]

        try:
            self._fix_route.fix_dispatcher.dispatch(operation, _continue, _finalize)
        except Exception as exc:
            raise AudisorRuntimeError(
                category="provider",
                stage="execution",
                code="fix_dispatch_failed",
                message=f"Fix dispatcher failed: {exc}",
                detail=type(exc).__name__,
            ) from exc

        if dispatched_result["path"] == "continue":
            return self._fix_continue(operation, dispatched_result["value"], receipt)
        return self._fix_finalize(operation, dispatched_result["value"], receipt)

    def _fix_continue(
        self,
        operation: Any,
        result: Any,
        receipt: MutationReceipt,
    ) -> AudisorOperationResult:
        """Translate an accepted Fix dispatcher result to canonical result.

        If a fix_continuation is configured, automatically launch Codex
        under the same operation ID after the handoff is persisted.
        """
        handoff_path: str | None = None
        if isinstance(result, dict):
            handoff_path = result.get("handoff_path")
        else:
            handoff_path = getattr(result, "handoff_path", None)
        artifacts: list[Mapping[str, Any]] = []
        if handoff_path:
            artifacts.append({
                "artifact_id": "qualified-fix-handoff",
                "artifact_type": "handoff",
                "reference": handoff_path,
            })

        execution: dict[str, Any] = {
            "fix_dispatched": True,
            "receipt_id": receipt.receipt_id,
            "receipt_digest": receipt.digest,
            "handoff_path": handoff_path,
            "codex_launched": False,
        }

        # Automatic host continuation: launch Codex if configured
        if handoff_path and self._fix_route is not None and self._fix_route.fix_continuation is not None:
            try:
                continuation_result = self._fix_route.fix_continuation.run(
                    operation_id=operation.operation_id,
                    handoff_path=handoff_path,
                    receipt=receipt,
                    allowed_target_paths=self._extract_target_paths({"fix": {
                        "plan": getattr(operation, "plan", None).__dict__ if hasattr(getattr(operation, "plan", None), "__dict__") else {},
                    }}) or list(receipt.authorized_paths),
                    working_directory=Path.cwd(),
                )
                execution["codex_launched"] = True
                execution["codex_result_reference"] = continuation_result.codex_result_reference
                execution["codex_envelope_path"] = continuation_result.codex_envelope_path
                execution["codex_exit_code"] = continuation_result.exit_code
                execution["codex_outcome"] = continuation_result.outcome
                # Propagate verification metadata from the continuation result
                execution["verification_performed"] = continuation_result.verification_performed
                execution["verification_passed"] = continuation_result.verification_passed
                execution["completion_claimed"] = continuation_result.completion_claimed
                if continuation_result.verification_result_reference:
                    execution["verification_result_reference"] = continuation_result.verification_result_reference
                # Add the Codex result as an artifact reference
                artifacts.append({
                    "artifact_id": "codex-fix-result",
                    "artifact_type": "execution",
                    "reference": continuation_result.codex_result_reference,
                })
                # Determine final status from the continuation result.
                # - Verification performed: use verification result
                # - Codex non-zero exit: failed
                # - No verifier configured: accepted (Codex launched but not verified)
                if continuation_result.verification_performed:
                    final_status = "completed" if continuation_result.completion_claimed else "failed"
                elif continuation_result.exit_code != 0:
                    final_status = "failed"
                else:
                    final_status = "accepted"
                return AudisorOperationResult(
                    operation_id=operation.operation_id,
                    status=final_status,
                    summary=f"Fix operation {final_status}",
                    artifacts=artifacts,
                    execution=execution,
                )
            except Exception as exc:
                # Codex launch failure → status = "failed", continuation not permitted
                error = AudisorRuntimeError(
                    category="provider",
                    stage="execution",
                    code="codex_launch_failed",
                    message=f"Codex launch failed: {exc}",
                    detail=type(exc).__name__,
                )
                return AudisorOperationResult(
                    operation_id=operation.operation_id,
                    status="failed",
                    error=error.to_error(),
                    summary=f"Fix failed: Codex launch failed: {exc}",
                    artifacts=artifacts,
                    execution={
                        **execution,
                        "codex_launched": False,
                        "codex_failure": str(exc),
                    },
                )

        return AudisorOperationResult(
            operation_id=operation.operation_id,
            status="accepted",
            summary="Fix operation accepted",
            artifacts=artifacts,
            execution=execution,
        )

    def _fix_finalize(
        self,
        operation: Any,
        result: Any,
        receipt: MutationReceipt,
    ) -> AudisorOperationResult:
        """Translate an unresolved Fix dispatcher result to canonical result."""
        error_info: dict[str, Any] = {}
        if isinstance(result, dict):
            error_info = result.get("error") or {}
        status: Literal["blocked", "failed"] = "blocked"
        code = error_info.get("code", "fix_unresolved") if error_info else "fix_unresolved"
        message = error_info.get("message", "Fix operation could not be resolved") if error_info else "Fix operation could not be resolved"
        evidence_ref: str | None = None
        if isinstance(result, dict):
            evidence_ref = result.get("evidence_reference")
        error = AudisorRuntimeError(
            category="contract",
            stage="execution",
            code=code,
            message=message,
        )
        return AudisorOperationResult(
            operation_id=operation.operation_id,
            status=status,
            error=error.to_error(),
            summary=f"Fix {status}: {message}",
            evidence=[
                OperationEvidence(
                    evidence_id="fix-evidence",
                    evidence_type="execution",
                    source="fix_dispatcher",
                    payload={"reference": evidence_ref} if evidence_ref else {},
                ).to_mapping()
            ],
            execution={
                "fix_dispatched": True,
                "receipt_id": receipt.receipt_id,
                "receipt_digest": receipt.digest,
            },
        )

    def _reconstruct_fix_operation(self, package: Mapping[str, Any]) -> Any:
        """Reconstruct typed Fix objects from the serialized Fix package.

        Imports from audisor_backend are lazy so that importing the
        canonical runtime does not fail when no Fix request is processed.
        """
        from audisor_backend.controllers.fix_host import AcceptedFixOperation
        from audisor_backend.schemas.fix.models import (
            Finding,
            FixScopedManifest,
            ImplementationPlan,
            MinorIssue,
            PlanStep,
            Statement,
        )

        try:
            operation_id = package["operation_id"]
            findings = [Finding(**item) for item in package["findings"]]
            manifest = FixScopedManifest(**package["manifest"])
            statements = tuple(Statement(**item) for item in package["statements"])  # type: ignore[assignment]
            plan_value = package["plan"]
            plan = ImplementationPlan(
                steps=[PlanStep(**item) for item in plan_value["steps"]],
                target_files=plan_value["target_files"],
                is_qualified=plan_value["is_qualified"],
                minor_issues=[MinorIssue(**item) for item in plan_value.get("minor_issues", [])],
            )
            return AcceptedFixOperation(  # type: ignore[arg-type]
                operation_id=operation_id,
                findings=findings,
                manifest=manifest,
                statements=statements,
                plan=plan,
                workspace_identity=package["workspace_identity"],
                authority_context=package["authority_context"],
                aflow_analysis_request=package.get("aflow_analysis_request"),
            )
        except (KeyError, TypeError, ValueError, ImportError) as exc:
            raise AudisorRuntimeError(
                category="validation",
                stage="request_translation",
                code="fix_contract_invalid",
                message=f"Fix payload is not a valid typed operation: {exc}",
            ) from exc

    # ------------------------------------------------------------------
    # Internal: execution dispatch
    # ------------------------------------------------------------------

    def _execute_mutation(
        self,
        context: AudisorOperationContext,
        receipt: MutationReceipt,
        *,
        audisor_config: AudisorConfig | None = None,
    ) -> AudisorOperationResult:
        """Execute a mutation operation (build or fix)."""
        # For mutation operations, we delegate to the model for plan analysis
        # but the actual mutation is gated by the receipt
        worker = self._get_worker(audisor_config)

        # Build the task for the worker
        task = self._build_task(context)

        # Execute model analysis
        try:
            output = worker.execute(task)
        except Exception as exc:
            # Convert worker exceptions to canonical errors
            error = self._policy.create_error(
                "provider_failed",
                f"Model execution failed: {exc}",
                detail=type(exc).__name__,
            )
            raise AudisorRuntimeError(
                category="provider",
                stage="model_invocation",
                code="provider_failed",
                message=error.error_detail.message,
                detail=error.error_detail.detail or "",
            ) from exc

        # Parse and validate output
        findings = self._parse_findings(output)

        # Persist execution artifact
        artifact_content = output.answer.encode("utf-8") if output.answer else b""
        artifact_ref = self._artifacts.persist(
            operation_id=context.operation_id,
            artifact_id="execution-result",
            content=artifact_content,
            artifact_type="report",
            extension=".json",
        )

        return AudisorOperationResult.from_success(
            operation_id=context.operation_id,
            findings=findings,
            evidence=[
                OperationEvidence(
                    evidence_id="worker-execution",
                    evidence_type="execution",
                    source="local_worker",
                    payload={
                        "model_id": worker.model_id,
                        "finish_reason": output.finish_reason,
                        "tool_call_present": output.tool_call_present,
                    },
                ).to_mapping()
            ],
            artifacts=[artifact_ref.to_mapping()],
            summary=f"Mutation operation completed: {context.mode}",
            execution={
                "receipt_id": receipt.receipt_id,
                "receipt_digest": receipt.digest,
                "model_id": worker.model_id,
                "finish_reason": output.finish_reason,
            },
        )

    def _execute_read_only(
        self,
        context: AudisorOperationContext,
        receipt: MutationReceipt,
        *,
        audisor_config: AudisorConfig | None = None,
    ) -> AudisorOperationResult:
        """Execute a read-only operation (analyze or validate)."""
        worker = self._get_worker(audisor_config)

        # Build the task for the worker
        task = self._build_task(context)

        # Execute model analysis
        try:
            output = worker.execute(task)
        except Exception as exc:
            error = self._policy.create_error(
                "provider_failed",
                f"Model execution failed: {exc}",
                detail=type(exc).__name__,
            )
            raise AudisorRuntimeError(
                category="provider",
                stage="model_invocation",
                code="provider_failed",
                message=error.error_detail.message,
                detail=error.error_detail.detail or "",
            ) from exc

        # Parse findings
        findings = self._parse_findings(output)

        # Persist analysis artifact
        artifact_content = output.answer.encode("utf-8") if output.answer else b""
        artifact_ref = self._artifacts.persist(
            operation_id=context.operation_id,
            artifact_id="analysis-result",
            content=artifact_content,
            artifact_type="report",
            extension=".json",
        )

        return AudisorOperationResult.from_success(
            operation_id=context.operation_id,
            findings=findings,
            evidence=[
                OperationEvidence(
                    evidence_id="worker-analysis",
                    evidence_type="analysis",
                    source="local_worker",
                    payload={
                        "model_id": worker.model_id,
                        "finish_reason": output.finish_reason,
                    },
                ).to_mapping()
            ],
            artifacts=[artifact_ref.to_mapping()],
            summary=f"Read-only operation completed: {context.mode}",
            validation={
                "receipt_id": receipt.receipt_id,
                "model_id": worker.model_id,
                "finish_reason": output.finish_reason,
            },
        )

    # ------------------------------------------------------------------
    # Internal: worker and task construction
    # ------------------------------------------------------------------

    def _get_worker(self, audisor_config: AudisorConfig | None = None) -> LocalWorker:
        """Get or create a LocalWorker for model execution."""
        # Honor an injected worker factory first, even when no explicit config
        # is supplied. This lets tests and host adapters inject deterministic
        # workers without requiring a full AudisorConfig.
        if self._worker_factory is not None:
            return self._worker_factory(audisor_config)

        if audisor_config is not None:
            return LocalWorker(
                base_url=audisor_config.base_url,
                model_id=audisor_config.model_id,
                timeout_seconds=audisor_config.timeout_seconds,
                max_tokens=audisor_config.max_tokens,
            )

        # Fallback to environment
        return LocalWorker.from_environment()

    def _build_task(self, context: AudisorOperationContext) -> TaskInput:
        """Build a TaskInput from the operation context."""
        # Serialize the request as the prompt
        prompt = json.dumps(context.request, ensure_ascii=False, indent=2)
        return TaskInput(task_id=context.operation_id, prompt=prompt)

    def _parse_findings(self, output: TaskOutput) -> list[Mapping[str, Any]]:
        """Parse findings from worker output."""
        findings: list[Mapping[str, Any]] = []
        if output.answer:
            try:
                # Try to parse as JSON for structured findings
                parsed = json.loads(output.answer)
                if isinstance(parsed, list):
                    findings = [dict(item) for item in parsed if isinstance(item, dict)]
                elif isinstance(parsed, dict):
                    findings = [parsed]
            except json.JSONDecodeError:
                # Unstructured output: treat as single finding
                findings = [{"type": "unstructured", "content": output.answer}]
        return findings

    # ------------------------------------------------------------------
    # Internal: result reconstruction from store
    # ------------------------------------------------------------------

    def _reconstruct_result(self, state: Any) -> AudisorOperationResult:
        """Reconstruct an AudisorOperationResult from a stored OperationState."""
        # For now, return a basic result indicating replay
        # In production, this would deserialize the full result from storage
        return AudisorOperationResult(
            operation_id=state.operation_id,
            status="completed" if state.status == "completed" else state.status,
            summary=f"Replayed operation (status={state.status})",
            idempotency_replay=True,
        )