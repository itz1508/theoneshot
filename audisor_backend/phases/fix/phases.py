"""Pure Fix phase contracts, kept separate from Build."""

import json
from dataclasses import replace
from hashlib import sha256
from typing import Any, Callable

from audisor_backend.adapters.aflow_fix import AFlowFixAdapter
from audisor_backend.schemas.fix.constants import AFLOW_GAP_CORRECTION_ATTEMPTS, PRESIM_RESCORE_ATTEMPTS
from audisor_backend.schemas.fix.models import *
from audisor_backend.policies.fix.completeness import evaluate_completeness
from audisor_backend.phases.fix.snapshot import create_scoped_snapshot


def make_scoped_manifest(findings: FindingsList, dependency_closure: list[str], input_hash: str) -> FixScopedManifest:
    manifest = FixScopedManifest(sorted({f.file for f in findings}), sorted(set(dependency_closure)), input_hash)
    manifest.validate()
    return manifest


def make_scoped_snapshot(repository_root, findings: FindingsList, dependency_closure: list[str], output_root, dependency_evidence=None):
    """Create the issue-only snapshot before analysis; never snapshot the repository root."""
    return create_scoped_snapshot(repository_root, findings, dependency_closure, output_root, dependency_evidence)


def make_statements(findings: FindingsList, manifest: FixScopedManifest) -> tuple[Statement, Statement, Statement]:
    findings_hash = sha256(json.dumps([f.__dict__ for f in findings], sort_keys=True, default=str).encode()).hexdigest()
    manifest_hash = sha256(json.dumps(manifest.__dict__, sort_keys=True).encode()).hexdigest()
    contents = (
        {"findings": [f.__dict__ for f in findings]},
        {"handoff": "repair only the identified findings"},
        {"requirements": "preserve scope and validate every finding"},
    )
    return tuple(Statement(kind, content, findings_hash, manifest_hash) for kind, content in zip(("dossier", "handoff", "llm"), contents))



def qualify_plan(plan: ImplementationPlan, manifest: FixScopedManifest) -> ImplementationPlan:
    issues = list(plan.minor_issues)
    steps = []
    for step in plan.steps:
        if step.acceptance_criterion is None:
            issues.append(MinorIssue("missing_acceptance_criterion", step.id, f"fallback: resolves {step.originating_finding_id}"))
            step = replace(step, acceptance_criterion=f"resolves {step.originating_finding_id}")
        steps.append(step)
    qualified = bool(steps) and all(step.target_file in manifest.files for step in steps)
    result = replace(plan, steps=steps, target_files=sorted({s.target_file for s in steps}), is_qualified=qualified, minor_issues=issues)
    result.validate(manifest)
    return result


def evaluate_aflow(plan: ImplementationPlan, findings: FindingsList, manifest: FixScopedManifest, adapter: AFlowFixAdapter) -> EvaluatedPlan:
    """Collect one advisory A-Flow evaluation; controller owns scoring/partition."""
    result = adapter.ignite(plan, findings, manifest)
    if result.gap_corrections_applied > AFLOW_GAP_CORRECTION_ATTEMPTS:
        raise ValueError("A-Flow gap correction limit exceeded")
    if result.status != "accepted":
        raise ValueError("A-Flow rejected Fix plan")
    return result


def pre_simulation(statements: tuple[Statement, Statement, Statement], plan: ImplementationPlan, outputs: AFlowOutputs, manifest: FixScopedManifest, scan_findings: FindingsList | None = None) -> CompletenessResult:
    """Controller-owned completeness scoring and eligibility gate."""
    same_refs = len({(s.findings_ref_hash, s.manifest_ref_hash) for s in statements}) == 1
    findings = scan_findings or []
    dependency_resolvable = not any(f.type == "dependency.unresolved" for f in findings)
    dependency_integrity = not any(f.type in {"dependency.conflicting_constraints", "dependency.version_mismatch", "dependency.duplicate_or_overlap"} for f in findings)
    dependency_signals = []
    for finding in findings:
        if finding.type in {"dependency.conflicting_constraints", "dependency.version_mismatch"}:
            dependency_signals.append({"type": "version_mismatch", "code": -10, "finding_id": finding.id, "file": finding.file})
        elif finding.type in {"dependency.duplicate_declaration", "dependency.duplicate_or_overlap"}:
            dependency_signals.append({"type": "duplicate_or_overlap", "code": -10, "finding_id": finding.id, "file": finding.file})
        elif finding.type == "dependency.upgrade_available":
            dependency_signals.append({"type": "upgrade_available", "code": -7, "finding_id": finding.id, "file": finding.file})
    values = {"scope_completeness": 1.0, "dependency_resolvability": 1.0 if dependency_resolvable else 0.0, "plan_completeness": 1.0 if plan.is_qualified else 0.0, "aflow_output_completeness": 1.0, "statement_consistency": 1.0 if same_refs else 0.0, "dependency_integrity": 1.0 if dependency_integrity else 0.0}
    result = evaluate_completeness(values, [])
    result = replace(result, dependency_resolvability={**result.dependency_resolvability, "flagged_issues": list(dependency_signals)}, dependency_integrity={**result.dependency_integrity, "flagged_issues": list(dependency_signals)})
    if result.status == "correctable":
        for _ in range(PRESIM_RESCORE_ATTEMPTS):
            result = evaluate_completeness(values, result.missing_info)
    return result


def verify_fix_output(input_hashes: dict[str, str], sandbox_output_hashes: dict[str, str], approved_changes: dict[str, str], success_definition_results: dict[str, list[dict[str, Any]]], unaffected_files_intact: bool) -> InspectionArtifact:
    expected = dict(input_hashes)
    expected.update(approved_changes)
    proof_satisfied = sandbox_output_hashes == expected
    all_checks = all(entry.get("passed") is True for entries in success_definition_results.values() for entry in entries)
    verified = proof_satisfied and unaffected_files_intact and all_checks
    reason = None if verified else ("hash proof failed" if not proof_satisfied else "unaffected file changed" if not unaffected_files_intact else "success validation failed")
    return InspectionArtifact(verified, {"equation": "hash(output) == hash(scoped_input ∪ approved_changes)", "satisfied": proof_satisfied}, unaffected_files_intact, success_definition_results, reason)
