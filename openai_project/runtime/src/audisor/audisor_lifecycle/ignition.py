"""Minimal primary-Codex ignition for the external Audisor contract adapter."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from .adapter import assemble_contract, verify_contract
from .build_analysis import BuildAnalysis, evaluate_build
from .contract import AudisorLifecycleError, requires_audisor_analysis
from .indicator import AudisorIndicator
from .local_invoker import invoke_audisor_analysis as local_invoke_audisor_analysis
from .artifacts import persist_audisor_stage
from .analysis_package import package_sha256
from audisor.workers.local import LocalWorker
from .operation import AudisorOperationContext, FrozenAudisorPolicy

CandidatePlanFactory = Callable[[Mapping[str, Any]], Mapping[str, Any]]
AudisorInvoker = Callable[..., Mapping[str, Any]]
_DEFAULT_LOCAL_INVOKER = local_invoke_audisor_analysis


@dataclass(frozen=True)
class IgnitionResult:
    lifecycle_selected: bool
    candidate_plan_source: str | None
    candidate_plan: Mapping[str, Any] | None
    execution_contract: Mapping[str, Any] | None
    implementation_eligible: bool
    build_analysis: BuildAnalysis | None = None
    original_plan: Mapping[str, Any] | None = None
    updated_original_plan: Mapping[str, Any] | None = None


def is_mutation_task(task_kind: str) -> bool:
    return task_kind in {"build", "fix"} or requires_audisor_analysis(task_kind)


def select_candidate_plan(task: Mapping[str, Any], supplied_plan: Mapping[str, Any] | None, create_candidate_plan: CandidatePlanFactory | None) -> tuple[Mapping[str, Any], str]:
    """Reuse usable supplied content; create exactly one plan only when absent."""
    if supplied_plan is not None:
        if not isinstance(supplied_plan, Mapping) or not supplied_plan:
            raise AudisorLifecycleError("supplied candidate plan is malformed")
        return supplied_plan, "supplied"
    if create_candidate_plan is None:
        raise AudisorLifecycleError("candidate plan is required before Audisor invocation")
    candidate = create_candidate_plan(task)
    if not isinstance(candidate, Mapping) or not candidate:
        raise AudisorLifecycleError("created candidate plan is malformed")
    return candidate, "created"


def _execution_ready(contract: Mapping[str, Any]) -> bool:
    readiness = contract.get("readiness")
    gates = readiness.get("execution_permitted_when") if isinstance(readiness, Mapping) else None
    return (
        verify_contract(contract)
        and isinstance(readiness, Mapping)
        and readiness.get("aflow_decision") == "no_material_gap"
        and readiness.get("contract_decision") == "no_material_gap"
        and readiness.get("unresolved_items") == []
        and isinstance(gates, Mapping)
        and all(value is True for value in gates.values())
    )


def ignite(*, operation_context: AudisorOperationContext | None = None, policy: FrozenAudisorPolicy | None = None, worker: LocalWorker | Any | None = None, task_kind: str | None = None, task: Mapping[str, Any] | None = None, repository_context: Mapping[str, Any] | None = None, supplied_plan: Mapping[str, Any] | None = None, create_candidate_plan: CandidatePlanFactory | None = None, invoke_audisor_analysis: AudisorInvoker | None = None, local_worker: LocalWorker | None = None) -> IgnitionResult:
    """Call Audisor then delegate contract validation to the existing adapter."""
    if operation_context is not None:
        task_kind = operation_context.operation_type
        task = operation_context.accepted_task
        repository_context = operation_context.repository_context
        supplied_plan = operation_context.accepted_plan
    if task_kind is None or task is None or repository_context is None:
        raise AudisorLifecycleError("Audisor operation context is incomplete")
    if not is_mutation_task(task_kind):
        return IgnitionResult(False, None, None, None, False)
    selected_policy = policy or FrozenAudisorPolicy(True, "local-openai-compatible", "qwen2.5-coder:7b", "http://127.0.0.1:11434")
    if not selected_policy.enabled:
        return IgnitionResult(False, None, None, None, False)
    callback = invoke_audisor_analysis or local_invoke_audisor_analysis
    # Build Audisor is an analysis helper only.  It consumes the supplied
    # original plan and returns gap/evaluation/success/validation/fixture data;
    # it never assembles an execution contract or grants continuation.
    if task_kind == "build" and callback is _DEFAULT_LOCAL_INVOKER:
        if supplied_plan is None:
            raise AudisorLifecycleError("Audisor Build requires the original plan")
        selected_worker = worker or local_worker
        if selected_worker is None:
            raise AudisorLifecycleError("Audisor worker must be constructed by the host")
        with AudisorIndicator():
            try:
                analysis = evaluate_build(
                    task=task,
                    original_plan=supplied_plan,
                    repository_context=repository_context,
                    worker=selected_worker,
                    operation_id=(operation_context.operation_id if operation_context is not None else "build-analysis"),
                )
            except Exception:
                raise
        return IgnitionResult(True, None, None, None, False, analysis, supplied_plan, analysis.updated_original_plan)
    candidate, source = select_candidate_plan(task, supplied_plan, create_candidate_plan)
    selected_worker = worker or local_worker
    if selected_worker is None and callback is local_invoke_audisor_analysis:
        raise AudisorLifecycleError("Audisor worker must be constructed by the host")
    decided_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    if callback is local_invoke_audisor_analysis:
        package = operation_context.analysis_package if operation_context is not None else None
        if package is None:
            raise AudisorLifecycleError("sealed Audisor package is required")
        if package.package_hash != package_sha256(package.canonical_payload):
            raise AudisorLifecycleError("sealed Audisor package hash mismatch")
        persist_audisor_stage(
            operation_context,
            "analysis-package",
            {"package_sha256": package.package_hash, "package": package.canonical_payload},
        )
    with AudisorIndicator():
        if callback is local_invoke_audisor_analysis:
            adapter_input = callback(
                task,
                candidate,
                repository_context,
                worker=selected_worker,
                operation_context=operation_context,
                package=operation_context.analysis_package,
                decided_at=decided_at,
            )
        else:
            adapter_input = callback(task, candidate, repository_context, worker=selected_worker)
        if not isinstance(adapter_input, Mapping):
            raise AudisorLifecycleError("Audisor returned malformed adapter input")
        contract = assemble_contract(adapter_input)["aflow_execution_contract"]
        if operation_context is not None and callback is local_invoke_audisor_analysis:
            persist_audisor_stage(operation_context, "execution-contract", contract)
    return IgnitionResult(True, source, candidate, contract, _execution_ready(contract))
