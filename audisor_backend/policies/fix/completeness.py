from typing import Any

from audisor_backend.schemas.fix.constants import COMPLETENESS_THRESHOLD, COMPLETENESS_WEIGHTS, STRUCTURAL_GATES
from audisor_backend.schemas.fix.models import CompletenessResult, FindingsList, FixScopedManifest, ImplementationPlan


def evaluate_completeness(values: dict[str, float], missing_info: list[str] | None = None) -> CompletenessResult:
    missing = list(missing_info or [])
    gates = {name: {"score": int(values.get(name, 0) >= 1), "missing_items": []} for name in STRUCTURAL_GATES}
    gradable = {name: {"score": float(values.get(name, 0)), "missing_items": []} for name in COMPLETENESS_WEIGHTS}
    score = sum(gradable[name]["score"] * weight for name, weight in COMPLETENESS_WEIGHTS.items())
    structural_bad = [name for name, gate in gates.items() if gate["score"] < 1]
    if structural_bad:
        status = "uncorrectable"
        missing.extend(structural_bad)
    elif score >= COMPLETENESS_THRESHOLD:
        status = "pass"
    else:
        status = "correctable"
    return CompletenessResult(
        scope_completeness=gradable["scope_completeness"],
        dependency_resolvability=gradable["dependency_resolvability"],
        plan_completeness=gates["plan_completeness"],
        aflow_output_completeness=gates["aflow_output_completeness"],
        statement_consistency=gates["statement_consistency"],
        dependency_integrity=gates["dependency_integrity"],
        missing_info=missing,
        score=score,
        status=status,
    )


def evaluate_fix_completeness(
    *,
    manifest: FixScopedManifest,
    plan: ImplementationPlan,
    findings: FindingsList,
    resolution_results: list[Any],
) -> CompletenessResult:
    """Evaluate Fix completeness using deterministic dependency resolution evidence.

    Unlike ``pre_simulation()``, this function consumes explicit per-finding
    resolution results rather than inferring dependency resolvability from
    the original finding type.  A ``dependency.unresolved`` finding whose
    resolution result shows ``resolved=True`` is treated as resolved.

    Only one evaluation pass is performed — no retry loop.
    """
    # Compute dependency resolvability from resolution results
    dependency_findings = [r for r in resolution_results if r.finding_id in {f.id for f in findings if f.type == "dependency.unresolved"}]
    if dependency_findings:
        resolved_count = sum(1 for r in dependency_findings if r.resolved)
        dependency_resolvable = resolved_count / len(dependency_findings)
    else:
        dependency_resolvable = 1.0

    # Dependency integrity: check for conflict/version/duplicate findings
    dependency_integrity_ok = not any(
        f.type in {"dependency.conflicting_constraints", "dependency.version_mismatch", "dependency.duplicate_or_overlap"}
        for f in findings
    )

    # plan may be an ImplementationPlan or a dict (from A-Flow igniter path)
    plan_is_qualified: bool
    if isinstance(plan, ImplementationPlan):
        plan_is_qualified = plan.is_qualified
    elif isinstance(plan, dict):
        plan_is_qualified = bool(plan.get("is_qualified", False))
    else:
        plan_is_qualified = bool(getattr(plan, "is_qualified", False))

    values = {
        "scope_completeness": 1.0,
        "dependency_resolvability": dependency_resolvable,
        "plan_completeness": 1.0 if plan_is_qualified else 0.0,
        "aflow_output_completeness": 1.0,
        "statement_consistency": 1.0,
        "dependency_integrity": 1.0 if dependency_integrity_ok else 0.0,
    }

    result = evaluate_completeness(values, [])

    # One-pass rule: do not retry correctable results
    return result

