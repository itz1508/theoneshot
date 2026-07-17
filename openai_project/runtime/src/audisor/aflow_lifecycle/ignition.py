"""Minimal primary-Codex ignition for the external A-Flow contract adapter."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping

from .adapter import assemble_contract, verify_contract
from .contract import AflowLifecycleError, requires_aflow_analysis

CandidatePlanFactory = Callable[[Mapping[str, Any]], Mapping[str, Any]]
AflowInvoker = Callable[[Mapping[str, Any], Mapping[str, Any], Mapping[str, Any]], Mapping[str, Any]]


@dataclass(frozen=True)
class IgnitionResult:
    lifecycle_selected: bool
    candidate_plan_source: str | None
    candidate_plan: Mapping[str, Any] | None
    execution_contract: Mapping[str, Any] | None
    implementation_eligible: bool


def is_mutation_task(task_kind: str) -> bool:
    return requires_aflow_analysis(task_kind)


def select_candidate_plan(task: Mapping[str, Any], supplied_plan: Mapping[str, Any] | None, create_candidate_plan: CandidatePlanFactory | None) -> tuple[Mapping[str, Any], str]:
    """Reuse usable supplied content; create exactly one plan only when absent."""
    if supplied_plan is not None:
        if not isinstance(supplied_plan, Mapping) or not supplied_plan:
            raise AflowLifecycleError("supplied candidate plan is malformed")
        return supplied_plan, "supplied"
    if create_candidate_plan is None:
        raise AflowLifecycleError("candidate plan is required before A-Flow invocation")
    candidate = create_candidate_plan(task)
    if not isinstance(candidate, Mapping) or not candidate:
        raise AflowLifecycleError("created candidate plan is malformed")
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


def ignite(*, task_kind: str, task: Mapping[str, Any], repository_context: Mapping[str, Any], supplied_plan: Mapping[str, Any] | None = None, create_candidate_plan: CandidatePlanFactory | None = None, invoke_aflow: AflowInvoker | None = None) -> IgnitionResult:
    """Call A-Flow then delegate contract validation to the existing adapter."""
    if not is_mutation_task(task_kind):
        return IgnitionResult(False, None, None, None, False)
    candidate, source = select_candidate_plan(task, supplied_plan, create_candidate_plan)
    if invoke_aflow is None:
        raise AflowLifecycleError("A-Flow invocation is required for a mutation task")
    adapter_input = invoke_aflow(task, candidate, repository_context)
    if not isinstance(adapter_input, Mapping):
        raise AflowLifecycleError("A-Flow returned malformed adapter input")
    contract = assemble_contract(adapter_input)["aflow_execution_contract"]
    return IgnitionResult(True, source, candidate, contract, _execution_ready(contract))
