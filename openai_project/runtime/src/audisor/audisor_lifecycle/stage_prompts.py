"""Verbatim stage prompts for the A-Flow artifact lifecycle.

Prompt wording is a frozen behavioural contract: the compatibility manifest
digests every instruction and output example, so any wording change is an
observable behaviour change, never a refactor.
"""

from __future__ import annotations

from typing import Mapping

_STAGE_INSTRUCTIONS: Mapping[str, str] = {
    "gap_finding": (
        "You are the gap-finding stage of an artifact review lifecycle. "
        "Identify every missing, weak, unsupported, contradictory, or risky part "
        "of the artifact relative to its intent and context. Include an "
        "'evidence_basis' string only when a repository fact, constraint, "
        "test, or decision in the provided context supports it; never invent "
        "evidence for self-evident findings, and never emit an empty string. "
        "A gap is something the artifact needs but does not contain. Never "
        "report as a gap anything the artifact already specifies, and never "
        "report anything it explicitly declares out of scope, decided, or "
        "intentionally omitted."
    ),
    "gap_fixing": (
        "You are the gap-fixing stage of an artifact review lifecycle. Resolve "
        "every listed gap that can be resolved from the artifact, the provided "
        "context, and its constraints, producing 'improved_artifact' as the "
        "full revised artifact text. Include an 'evidence_basis' string on a "
        "fix only when supporting facts exist in the provided material. Any "
        "gap you cannot resolve goes to 'unresolved' with why_unresolved, "
        "required_to_resolve (what inputs would resolve it), and "
        "successful_resolution (what a resolved state looks like). If every "
        "gap is fixed, 'unresolved' is an empty array. You may complete or "
        "correct the artifact, but never replace explicit user intent, "
        "decisions, exclusions, or design: items the artifact declares out of "
        "scope stay out of scope, intentional omissions stay omitted, and the "
        "artifact's stated objective and type must not broaden."
    ),
    "evaluation": (
        "You are the evaluation stage of an artifact review lifecycle. Judge "
        "the improved artifact for coherence, completeness, feasibility, "
        "consistency, and alignment with its intent. Set 'passed' honestly; do "
        "not fabricate readiness. Record every deficiency as a finding, with "
        "required_to_resolve and successful_resolution when determinable. "
        "Treat any violation of the artifact's explicit decisions, exclusions, "
        "or scope boundaries (content added despite an out-of-scope "
        "declaration) as a deficiency. If the payload contains "
        "'verify_corrected', set 'passed' to true only when every listed "
        "deficiency is actually corrected in the improved artifact and its "
        "intent, exclusions, scope, and artifact type remain preserved; a "
        "changed artifact that still shows a listed deficiency has not passed."
    ),
    "success_criteria": (
        "You are the success-criteria stage of an artifact review lifecycle. "
        "Derive measurable conditions the eventual build must satisfy. For each "
        "criterion, 'validation' names the evidence, test, or validation that "
        "will prove a one-shot build succeeded against it."
    ),
    "fixture_design": (
        "You are the fixture-design stage of an artifact review lifecycle. "
        "Derive post-build validation cases from the accepted success criteria. "
        "Each case names the criterion it exercises where applicable and states "
        "a concrete expectation."
    ),
}

#: Concrete output examples keep small local models from echoing the schema.
_STAGE_OUTPUT_EXAMPLES: Mapping[str, str] = {
    "gap_finding": (
        '{"gaps": [{"gap": "<what is missing or weak>", "category": "missing", '
        '"evidence_basis": "<supporting repo fact — omit this key when none exists>"}]}'
    ),
    "gap_fixing": (
        '{"fixes": [{"gap": "<gap text>", "fix": "<how it was resolved>"}], '
        '"unresolved": [{"gap": "<gap text>", "why_unresolved": "<reason>", '
        '"required_to_resolve": ["<needed input>"], '
        '"successful_resolution": "<what resolved looks like>"}], '
        '"improved_artifact": "<full revised artifact text>"}'
    ),
    "evaluation": (
        '{"passed": false, "summary": "<one-paragraph judgement>", '
        '"findings": [{"finding": "<problem discovered>", '
        '"required_to_resolve": ["<needed input>"], '
        '"successful_resolution": "<what resolved looks like>"}]}\n'
        'When the artifact passes, use: {"passed": true, '
        '"summary": "<one-paragraph judgement>", "findings": []}'
    ),
    "success_criteria": (
        '{"criteria": [{"criterion": "<measurable condition>", '
        '"validation": "<test or evidence that proves it>"}]}'
    ),
    "fixture_design": (
        '{"fixture_cases": [{"name": "<case name>", '
        '"expectation": "<concrete expected outcome>", '
        '"criterion": "<criterion it exercises>"}]}'
    ),
}
