from audisor_backend.schemas.fix.constants import COMPLETENESS_THRESHOLD, COMPLETENESS_WEIGHTS, STRUCTURAL_GATES
from audisor_backend.schemas.fix.models import CompletenessResult


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

