"""Controller-owned Fix lifecycle; agents never select release or authority."""

from audisor_backend.adapters.aflow_fix import AFlowFixAdapter
from audisor_backend.phases.fix.phases import evaluate_aflow, pre_simulation, qualify_plan, verify_fix_output
from audisor_backend.phases.fix.phases import make_scoped_snapshot, make_statements
from audisor_backend.scanning.scanner import DeterministicScanner
from audisor_backend.scanning.dependency_closure import resolve_dependency_details
from audisor_backend.schemas.fix.models import FinalResult, FindingsList, FixScopedManifest, ImplementationPlan, AFlowOutputs


class FixController:
    def __init__(self, mode: str = "automatic"):
        if mode not in ("automatic", "manual"):
            raise ValueError("mode must be automatic or manual")
        self.mode = mode

    def prepare_from_scan(self, repository_root, findings: FindingsList, dependency_items: list[str], snapshot_root):
        """Persist the issue-only snapshot before any Fix/A-Flow invocation."""
        snapshot = make_scoped_snapshot(repository_root, findings, dependency_items, snapshot_root)
        manifest = snapshot.manifest()
        statements = make_statements(findings, manifest)
        return snapshot, manifest, statements

    def scan_and_prepare(self, repository_root, snapshot_root, scanner: DeterministicScanner | None = None):
        """Run the read-only scanner and prepare only its affected closure."""
        report = (scanner or DeterministicScanner()).scan(repository_root)
        dependency_items, dependency_evidence = resolve_dependency_details(repository_root, report.findings)
        snapshot = make_scoped_snapshot(repository_root, report.findings, dependency_items, snapshot_root, dependency_evidence)
        manifest = snapshot.manifest()
        statements = make_statements(report.findings, manifest)
        return report, snapshot, manifest, statements

    def run_from_scan(self, repository_root, snapshot_root, plan: ImplementationPlan, adapter: AFlowFixAdapter, outputs: AFlowOutputs, inspection, *, scanner: DeterministicScanner | None = None, user_decision: str | None = None):
        """Run the complete controller path after creating the scoped snapshot."""
        report, snapshot, manifest, statements = self.scan_and_prepare(repository_root, snapshot_root, scanner)
        result = self.run(report.findings, manifest, plan, statements, adapter, outputs, inspection, user_decision)
        return result, report, snapshot, manifest, statements

    def run(self, findings: FindingsList, manifest: FixScopedManifest, plan: ImplementationPlan, statements, adapter: AFlowFixAdapter, outputs: AFlowOutputs, inspection, user_decision: str | None = None) -> FinalResult:
        if not manifest.file_hashes:
            return FinalResult(False, self.mode, [], [f.id for f in findings], "information_gap", {"reason": "scoped_snapshot_required", "disclaimer": "advisory only"})
        qualified = qualify_plan(plan, manifest)
        evaluated = evaluate_aflow(qualified, findings, manifest, adapter)
        adapter.validate_outputs(outputs, findings)
        completeness = pre_simulation(statements, evaluated.plan, outputs, manifest, findings)
        if completeness.status != "pass":
            return FinalResult(False, self.mode, [], [f.id for f in findings], "information_gap", {"perfect": False, "minor_issues": qualified.minor_issues, "disclaimer": "advisory only"})
        if not inspection.verified:
            return FinalResult(False, self.mode, [], [f.id for f in findings], "verification_failure", {"perfect": False, "minor_issues": qualified.minor_issues, "disclaimer": "advisory only"})
        if self.mode == "manual" and user_decision != "apply":
            return FinalResult(False, self.mode, [], [f.id for f in findings], "user_skip", {"perfect": False, "minor_issues": qualified.minor_issues, "disclaimer": "advisory only"})
        return FinalResult(True, self.mode, [f.id for f in findings], [], "none", {"perfect": not qualified.minor_issues, "minor_issues": qualified.minor_issues, "disclaimer": "advisory only"})

    def accept(self, operation, dispatcher, continue_implementation, finalize_unresolved):
        """External accepted-Fix controller boundary; validation remains below it."""
        return dispatcher.dispatch(operation, continue_implementation, finalize_unresolved)
