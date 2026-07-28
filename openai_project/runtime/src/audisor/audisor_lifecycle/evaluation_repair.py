"""Evaluation repair: gap synthesis and the single bounded repair cycle."""

from __future__ import annotations

from typing import Any, Callable, Mapping

from .identity import _content_digest
from .result_builder import _LifecycleRunState

_RunStage = Callable[[str, Mapping[str, Any]], Mapping[str, Any]]


def _normalize_unresolved(entries: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "gap": entry["gap"],
            "why_unresolved": entry["why_unresolved"],
            "required_to_resolve": list(entry["required_to_resolve"]),
            "successful_resolution": entry["successful_resolution"],
        }
        for entry in entries
    ]


def _evaluation_findings_as_unresolved(
    findings: list[Mapping[str, Any]],
    why_unresolved: str = "discovered during evaluation",
) -> list[dict[str, Any]]:
    """Late-discovered gaps: failed-evaluation findings map to the unresolved shape."""
    return [
        {
            "gap": finding["finding"],
            "why_unresolved": why_unresolved,
            "required_to_resolve": list(finding.get("required_to_resolve", [])),
            "successful_resolution": finding.get(
                "successful_resolution", "the finding no longer applies to the artifact"
            ),
        }
        for finding in findings
    ]


def _refix_contract(findings: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Structured refix contract for evaluation-discovered deficiencies.

    Deterministic mapping from evaluation findings — the model never
    classifies resolvability; every first-evaluation finding gets exactly one
    bounded re-fix attempt.
    """
    return [
        {
            "finding_id": f"evaluation.{index}",
            "deficiency": finding["finding"],
            "evidence": list(finding.get("evidence", [])),
            "required_correction": (
                "; ".join(finding.get("required_to_resolve", []))
                or "correct the deficiency so it no longer applies"
            ),
            "success_condition": finding.get(
                "successful_resolution", "the deficiency no longer applies to the artifact"
            ),
        }
        for index, finding in enumerate(findings, start=1)
    ]


def _unresolved_result(
    content: str,
    gap_findings: list[dict[str, Any]],
    gap_fixes: list[dict[str, Any]],
    unresolved_gaps: list[dict[str, Any]],
) -> dict[str, Any]:
    """Barrier result shape: nothing downstream runs while gaps remain."""
    return {
        "status": "unresolved_gap",
        "original_artifact": content,
        "gap_findings": gap_findings,
        "gap_fixes": gap_fixes,
        "unresolved_gaps": unresolved_gaps,
        "evaluation": None,
        "success_criteria": None,
        "fixture_cases": None,
    }


def _run_repair_cycle(
    run_stage: _RunStage,
    common: Mapping[str, Any],
    content: str,
    gap_findings: list[dict[str, Any]],
    gap_fixes: list[dict[str, Any]],
    improved_artifact: str,
    evaluated: Mapping[str, Any],
    state: _LifecycleRunState,
) -> tuple[dict[str, Any] | None, str, Mapping[str, Any]]:
    """Bounded repair cycle (exactly one): evaluation-discovered deficiencies
    become a structured refix contract and go back to gap_fixing once.

    Returns ``(early_result, improved_artifact, evaluated)``: a non-``None``
    early result (error or unresolved) ends the run via ``finish``;
    ``gap_fixes`` is extended in place and ``state`` carries the evidence.
    """
    refix_contract = _refix_contract(list(evaluated["findings"]))
    eval_gaps = [{"gap": item["deficiency"]} for item in refix_contract]
    before_refix = _content_digest(improved_artifact)
    refixed = run_stage(
        "gap_fixing",
        {
            **common,
            "artifact": improved_artifact,
            "gaps": eval_gaps,
            "refix_contract": refix_contract,
            "previously_fixed": [dict(fix) for fix in gap_fixes],
        },
    )
    if refixed.get("status") == "error":
        return dict(refixed), improved_artifact, evaluated
    state.repair_cycles = 1
    # The bounded cycle is proven, not just counted: structured findings,
    # the before/after digest comparison, the re-fix output, and (once it
    # runs) the final evaluation all stay on the record — including when
    # the outcome is unresolved_gap (the failed evaluation is evidence).
    state.evaluation_repair = {
        "findings": refix_contract,
        "before_content_digest": before_refix,
        "after_content_digest": _content_digest(refixed["improved_artifact"]),
        "gap_fixes": [dict(fix) for fix in refixed["fixes"]],
        "final_evaluation": None,
    }
    if refixed["unresolved"]:
        return (
            _unresolved_result(
                content, gap_findings, gap_fixes, _normalize_unresolved(refixed["unresolved"])
            ),
            improved_artifact,
            evaluated,
        )
    return _verify_repair(
        run_stage, common, content, gap_findings, gap_fixes,
        improved_artifact, refixed, before_refix, refix_contract, evaluated, state,
    )


def _verify_repair(
    run_stage: _RunStage, common: Mapping[str, Any], content: str,
    gap_findings: list[dict[str, Any]], gap_fixes: list[dict[str, Any]],
    improved_artifact: str, refixed: Mapping[str, Any], before_refix: str,
    refix_contract: list[dict[str, Any]], evaluated: Mapping[str, Any],
    state: _LifecycleRunState,
) -> tuple[dict[str, Any] | None, str, Mapping[str, Any]]:
    """Second half of the repair cycle: digest-progress rule, then the
    verifying second evaluation. An unchanged canonical artifact is no
    progress, in code, not model opinion — and a changed digest alone is
    never success; the second evaluation must verify each deficiency."""
    if _content_digest(refixed["improved_artifact"]) == before_refix:
        unresolved = _evaluation_findings_as_unresolved(
            list(evaluated["findings"]),
            "discovered during evaluation; repair cycle made no progress",
        )
        return (
            _unresolved_result(content, gap_findings, gap_fixes, unresolved),
            improved_artifact,
            evaluated,
        )
    gap_fixes.extend(dict(fix) for fix in refixed["fixes"])
    improved_artifact = refixed["improved_artifact"]
    evaluated = run_stage(
        "evaluation",
        {
            **common,
            "improved_artifact": improved_artifact,
            "gap_fixes": gap_fixes,
            "verify_corrected": refix_contract,
        },
    )
    if evaluated.get("status") == "error":
        return dict(evaluated), improved_artifact, evaluated
    # Preserve the final evaluation as diagnostic evidence whether it
    # passed or failed — a nulled top-level evaluation must never erase
    # the reason an unresolved result failed.
    state.evaluation_repair["final_evaluation"] = {
        "passed": bool(evaluated["passed"]),
        "summary": evaluated["summary"],
        "findings": [dict(finding) for finding in evaluated["findings"]],
    }
    if not evaluated["passed"]:
        # Still deficient after the one bounded cycle: unresolved, and
        # nothing downstream runs.
        unresolved = _evaluation_findings_as_unresolved(
            list(evaluated["findings"]),
            "discovered during evaluation; unresolved after one repair cycle",
        )
        return (
            _unresolved_result(content, gap_findings, gap_fixes, unresolved),
            improved_artifact,
            evaluated,
        )
    return None, improved_artifact, evaluated
