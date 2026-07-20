"""Runtime-validated dataclasses for the supplied scoped-verification-pipeline."""

from dataclasses import dataclass, field
from typing import Any, Literal

FindingType = str
Severity = str
ResolutionMethod = Literal["rescan", "test", "assertion"]


@dataclass(frozen=True)
class Finding:
    id: str
    type: FindingType
    file: str
    severity: Severity
    evidence: Any


FindingsList = list[Finding]


@dataclass(frozen=True)
class FixScopedManifest:
    files: list[str]
    dependency_closure: list[str]
    input_hash: str
    file_hashes: dict[str, str] = field(default_factory=dict)
    dependency_evidence: dict[str, list[dict[str, str]]] = field(default_factory=dict)

    def validate(self) -> None:
        if not self.input_hash or len(set(self.files)) != len(self.files):
            raise ValueError("invalid scoped manifest")
        if not set(self.files).issubset(set(self.dependency_closure)):
            raise ValueError("scoped files must be in dependency closure")
        if self.file_hashes and set(self.file_hashes) != set(self.dependency_closure):
            raise ValueError("scoped file hashes must match dependency closure")
        if any(len(value) != 64 or any(char not in "0123456789abcdef" for char in value) for value in self.file_hashes.values()):
            raise ValueError("scoped file hashes must be SHA-256 values")
        if any(path not in self.dependency_closure for path in self.dependency_evidence):
            raise ValueError("dependency evidence contains an out-of-scope file")
        for path, records in self.dependency_evidence.items():
            for record in records:
                required = {"originating_finding_id", "dependency_source", "dependency_target", "inclusion_reason", "resolution_evidence"}
                if set(record) != required or not all(isinstance(value, str) and value for value in record.values()):
                    raise ValueError(f"invalid dependency evidence:{path}")


@dataclass(frozen=True)
class Statement:
    type: str
    content: dict[str, Any]
    findings_ref_hash: str
    manifest_ref_hash: str


@dataclass(frozen=True)
class PlanStep:
    id: str
    action: str
    target_file: str
    originating_finding_id: str
    acceptance_criterion: str | None


@dataclass(frozen=True)
class MinorIssue:
    type: Literal["naming", "syntax", "keyword", "style", "missing_acceptance_criterion"]
    step_id: str
    detail: str


@dataclass(frozen=True)
class ImplementationPlan:
    steps: list[PlanStep]
    target_files: list[str]
    is_qualified: bool
    minor_issues: list[MinorIssue] = field(default_factory=list)

    def validate(self, manifest: FixScopedManifest) -> None:
        if not self.steps or not self.target_files:
            raise ValueError("plan_incomplete")
        if not self.is_qualified:
            raise ValueError("plan is not qualified")
        if any(step.target_file not in manifest.files for step in self.steps):
            raise ValueError("plan target is outside scoped manifest")
        if set(self.target_files) != {step.target_file for step in self.steps}:
            raise ValueError("plan target_files mismatch")
        if any(not step.originating_finding_id for step in self.steps):
            raise ValueError("originating_finding_id is required")


@dataclass(frozen=True)
class EvaluatedPlan:
    plan: ImplementationPlan
    gap_corrections_applied: int
    status: Literal["accepted", "rejected"]


@dataclass(frozen=True)
class FindingCheck:
    finding_id: str
    resolution_method: ResolutionMethod
    check: str
    expected_result: str


@dataclass(frozen=True)
class ValidationSpec:
    id: str
    command_or_assertion: str
    expected_result: str


@dataclass(frozen=True)
class SuccessDefinition:
    finding_checks: list[FindingCheck]
    validations: list[ValidationSpec]
    must_not_regress: list[str]
    success_rule: str

    def covers(self, findings: FindingsList) -> bool:
        return {c.finding_id for c in self.finding_checks} >= {f.id for f in findings}


@dataclass(frozen=True)
class AFlowOutputs:
    trajectory: list[Any]
    success_definition: SuccessDefinition
    validations: list[ValidationSpec]


@dataclass(frozen=True)
class CompletenessResult:
    scope_completeness: dict[str, Any]
    dependency_resolvability: dict[str, Any]
    plan_completeness: dict[str, Any]
    aflow_output_completeness: dict[str, Any]
    statement_consistency: dict[str, Any]
    dependency_integrity: dict[str, Any]
    missing_info: list[str]
    score: float
    status: Literal["pass", "correctable", "uncorrectable"]


@dataclass(frozen=True)
class SandboxResult:
    commands: list[str]
    diffs: list[str]
    stdout: str
    stderr: str
    output_hashes: dict[str, str]


@dataclass(frozen=True)
class InspectionArtifact:
    verified: bool
    proof: dict[str, Any]
    unaffected_files_intact: bool
    success_definition_results: dict[str, list[dict[str, Any]]]
    reason: str | None


@dataclass(frozen=True)
class FinalResult:
    released: bool
    mode: Literal["automatic", "manual"]
    resolved_items: list[str]
    unresolved_items: list[str]
    unresolved_reason: Literal["none", "information_gap", "verification_failure", "user_skip"]
    quality_notes: dict[str, Any]
