"""Deterministic validation grounding for the Fix verification contract."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Literal, Sequence

from audisor_backend.schemas.fix.models import (
    Finding, FindingCheck, FindingsList, FixScopedManifest,
    ImplementationPlan, SuccessDefinition, ValidationSpec,
)


class GroundingError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code


@dataclass(frozen=True)
class ScannerCheckSource:
    finding_id: str
    finding_type: str
    repro: str
    scoped_path: str


@dataclass(frozen=True)
class ConfiguredTestSource:
    tokens: tuple[str, ...]


@dataclass(frozen=True)
class RecordedTestSource:
    finding_id: str
    tokens: tuple[str, ...]


@dataclass(frozen=True)
class TestFileSource:
    relative_path: str


@dataclass(frozen=True)
class PlanAcceptanceSource:
    step_id: str
    finding_id: str
    target_file: str
    acceptance_criterion: str


@dataclass(frozen=True)
class DeterministicAssertionSource:
    assertion_form: str
    relative_path: str
    literal: str | None


@dataclass(frozen=True)
class ValidationSourceCatalog:
    scanner_checks: list[ScannerCheckSource] = field(default_factory=list)
    configured_tests: list[ConfiguredTestSource] = field(default_factory=list)
    recorded_tests: list[RecordedTestSource] = field(default_factory=list)
    test_files: list[TestFileSource] = field(default_factory=list)
    plan_acceptances: list[PlanAcceptanceSource] = field(default_factory=list)
    deterministic_assertions: list[DeterministicAssertionSource] = field(default_factory=list)

    def to_mapping(self) -> dict[str, Any]:
        return {
            "scanner_checks": [{"finding_id": s.finding_id, "finding_type": s.finding_type, "repro": s.repro, "scoped_path": s.scoped_path} for s in self.scanner_checks],
            "configured_tests": [list(s.tokens) for s in self.configured_tests],
            "recorded_tests": [{"finding_id": s.finding_id, "tokens": list(s.tokens)} for s in self.recorded_tests],
            "test_files": [{"relative_path": s.relative_path} for s in self.test_files],
            "plan_acceptances": [{"step_id": s.step_id, "finding_id": s.finding_id, "target_file": s.target_file, "acceptance_criterion": s.acceptance_criterion} for s in self.plan_acceptances],
            "deterministic_assertions": [{"assertion_form": s.assertion_form, "relative_path": s.relative_path, "literal": s.literal} for s in self.deterministic_assertions],
        }


def build_validation_source_catalog(*, repository_root: Path, findings: FindingsList, manifest: FixScopedManifest, plan: ImplementationPlan, configured_test_commands: Sequence[tuple[str, ...]] = ()) -> ValidationSourceCatalog:
    scanner_checks: list[ScannerCheckSource] = []
    recorded_tests: list[RecordedTestSource] = []
    test_files: list[TestFileSource] = []
    plan_acceptances: list[PlanAcceptanceSource] = []
    deterministic_assertions: list[DeterministicAssertionSource] = []
    allowed_paths = set(manifest.files) | set(manifest.dependency_closure)

    for finding in findings:
        evidence = finding.evidence if isinstance(finding.evidence, dict) else {}
        repro = evidence.get("repro", "")
        if isinstance(repro, str) and repro:
            scanner_checks.append(ScannerCheckSource(finding_id=finding.id, finding_type=finding.type, repro=repro, scoped_path=finding.file))
        command = evidence.get("command")
        if isinstance(command, list) and all(isinstance(t, str) for t in command):
            recorded_tests.append(RecordedTestSource(finding_id=finding.id, tokens=tuple(command)))

    configured_tests = [ConfiguredTestSource(tokens=tuple(cmd)) for cmd in configured_test_commands]

    for rel_path in manifest.dependency_closure:
        if _is_test_file(rel_path):
            test_files.append(TestFileSource(relative_path=rel_path))

    for step in plan.steps:
        if step.acceptance_criterion:
            plan_acceptances.append(PlanAcceptanceSource(step_id=step.id, finding_id=step.originating_finding_id, target_file=step.target_file, acceptance_criterion=step.acceptance_criterion))

    for rel_path in allowed_paths:
        for form in ("scanner_clear", "file_exists", "file_contains", "file_not_contains", "python_compiles", "json_parses"):
            deterministic_assertions.append(DeterministicAssertionSource(assertion_form=form, relative_path=rel_path, literal=None))

    return ValidationSourceCatalog(scanner_checks=scanner_checks, configured_tests=configured_tests, recorded_tests=recorded_tests, test_files=test_files, plan_acceptances=plan_acceptances, deterministic_assertions=deterministic_assertions)


def _is_test_file(rel_path: str) -> bool:
    name = PurePosixPath(rel_path).name.lower()
    return name.startswith("test_") or name.endswith("_test.py") or name == "tests.py"


SUPPORTED_ASSERTION_FORMS = {"scanner_clear", "file_exists", "file_contains", "file_not_contains", "python_compiles", "json_parses"}
_ASSERTION_RE = re.compile(r"^(?P<form>scanner_clear|file_exists|file_contains|file_not_contains|python_compiles|json_parses)::(?P<rest>.+)$")


def parse_assertion(assertion: str) -> tuple[str, str, str | None] | None:
    match = _ASSERTION_RE.match(assertion)
    if not match:
        return None
    form = match.group("form")
    rest = match.group("rest")
    if form in ("file_contains", "file_not_contains"):
        parts = rest.split("::", 1)
        if len(parts) != 2:
            return None
        return form, parts[0], parts[1]
    return form, rest, None


def is_valid_scoped_path(path: str, allowed_paths: set[str]) -> bool:
    if not path or path.startswith("/") or Path(path).is_absolute():
        return False
    if ".." in PurePosixPath(path).parts:
        return False
    if path.startswith("\\"):
        return False
    return path in allowed_paths


def normalize_command_to_tokens(command: str) -> tuple[str, ...] | None:
    import shlex
    try:
        tokens = shlex.split(command)
        if not tokens:
            return None
        return tuple(tokens)
    except ValueError:
        return None


def match_command_exact(command_tokens: tuple[str, ...], authorized_commands: list[tuple[str, ...]]) -> bool:
    return command_tokens in authorized_commands


@dataclass(frozen=True)
class FindingCheckGrounding:
    finding_id: str
    resolution_method: str
    source_type: Literal["scanner_check", "plan_acceptance", "deterministic_assertion", "recorded_test"]
    source_reference: str
    authorized_tokens: tuple[str, ...] | None
    scoped_paths: tuple[str, ...]


@dataclass(frozen=True)
class ValidationGrounding:
    validation_id: str
    source_type: Literal["configured_test", "recorded_test", "deterministic_assertion"]
    source_reference: str
    authorized_tokens: tuple[str, ...] | None
    scoped_paths: tuple[str, ...]


@dataclass(frozen=True)
class VerificationGrounding:
    finding_checks: list[FindingCheckGrounding]
    validations: list[ValidationGrounding]

    def to_mapping(self) -> dict[str, Any]:
        return {
            "finding_checks": [{"finding_id": g.finding_id, "resolution_method": g.resolution_method, "source_type": g.source_type, "source_reference": g.source_reference, "authorized_tokens": list(g.authorized_tokens) if g.authorized_tokens else None, "scoped_paths": list(g.scoped_paths)} for g in self.finding_checks],
            "validations": [{"validation_id": g.validation_id, "source_type": g.source_type, "source_reference": g.source_reference, "authorized_tokens": list(g.authorized_tokens) if g.authorized_tokens else None, "scoped_paths": list(g.scoped_paths)} for g in self.validations],
        }


REQUIRED_SUCCESS_RULE = "all_finding_checks_and_validations_pass"


class ValidationGroundingResolver:
    def resolve(self, *, repository_root: Path, findings: FindingsList, manifest: FixScopedManifest, plan: ImplementationPlan, success_definition: SuccessDefinition, catalog: ValidationSourceCatalog) -> VerificationGrounding:
        finding_ids = {f.id for f in findings}
        allowed_paths = set(manifest.files) | set(manifest.dependency_closure)

        if success_definition.success_rule != REQUIRED_SUCCESS_RULE:
            raise GroundingError("unsupported_success_rule", f"success_rule must be '{REQUIRED_SUCCESS_RULE}', got: {success_definition.success_rule!r}")

        check_groundings: list[FindingCheckGrounding] = []
        for check in success_definition.finding_checks:
            grounding = self._ground_finding_check(check=check, findings=findings, finding_ids=finding_ids, allowed_paths=allowed_paths, catalog=catalog)
            check_groundings.append(grounding)

        validation_groundings: list[ValidationGrounding] = []
        for val in success_definition.validations:
            grounding = self._ground_validation(val=val, findings=findings, allowed_paths=allowed_paths, catalog=catalog)
            validation_groundings.append(grounding)

        return VerificationGrounding(finding_checks=check_groundings, validations=validation_groundings)

    def _ground_finding_check(self, *, check: FindingCheck, findings: FindingsList, finding_ids: set[str], allowed_paths: set[str], catalog: ValidationSourceCatalog) -> FindingCheckGrounding:
        if check.finding_id not in finding_ids:
            raise GroundingError("validation_not_grounded", f"finding_check references unknown finding_id: {check.finding_id}")
        finding = next(f for f in findings if f.id == check.finding_id)

        for src in catalog.scanner_checks:
            if src.finding_id == check.finding_id:
                return FindingCheckGrounding(finding_id=check.finding_id, resolution_method=check.resolution_method, source_type="scanner_check", source_reference=f"scanner:{src.finding_id}", authorized_tokens=None, scoped_paths=(src.scoped_path,))

        for src in catalog.plan_acceptances:
            if src.finding_id == check.finding_id:
                return FindingCheckGrounding(finding_id=check.finding_id, resolution_method=check.resolution_method, source_type="plan_acceptance", source_reference=f"plan:{src.step_id}", authorized_tokens=None, scoped_paths=(src.target_file,))

        parsed = parse_assertion(check.check)
        if parsed is not None:
            form, rel_path, literal = parsed
            if form == "scanner_clear":
                if rel_path != check.finding_id:
                    raise GroundingError("validation_not_grounded", f"scanner_clear assertion references {rel_path}, expected {check.finding_id}")
                return FindingCheckGrounding(finding_id=check.finding_id, resolution_method=check.resolution_method, source_type="deterministic_assertion", source_reference=f"assertion:{check.check}", authorized_tokens=None, scoped_paths=(finding.file,))
            else:
                if not is_valid_scoped_path(rel_path, allowed_paths):
                    raise GroundingError("validation_not_grounded", f"assertion references invalid or out-of-scope path: {rel_path}")
                return FindingCheckGrounding(finding_id=check.finding_id, resolution_method=check.resolution_method, source_type="deterministic_assertion", source_reference=f"assertion:{check.check}", authorized_tokens=None, scoped_paths=(rel_path,))

        for src in catalog.recorded_tests:
            if src.finding_id == check.finding_id:
                tokens = normalize_command_to_tokens(check.check)
                if tokens is not None and match_command_exact(tokens, [src.tokens]):
                    return FindingCheckGrounding(finding_id=check.finding_id, resolution_method=check.resolution_method, source_type="recorded_test", source_reference=f"recorded:{src.finding_id}", authorized_tokens=src.tokens, scoped_paths=(finding.file,))

        raise GroundingError("validation_not_grounded", f"finding_check for {check.finding_id} cannot be grounded: {check.check!r}")

    def _ground_validation(self, *, val: ValidationSpec, findings: FindingsList, allowed_paths: set[str], catalog: ValidationSourceCatalog) -> ValidationGrounding:
        parsed = parse_assertion(val.command_or_assertion)
        if parsed is not None:
            form, rel_path, literal = parsed
            if form == "scanner_clear":
                raise GroundingError("validation_not_grounded", f"scanner_clear assertion is not valid for validation: {val.command_or_assertion!r}")
            if not is_valid_scoped_path(rel_path, allowed_paths):
                raise GroundingError("validation_not_grounded", f"assertion references invalid or out-of-scope path: {rel_path}")
            return ValidationGrounding(validation_id=val.id, source_type="deterministic_assertion", source_reference=f"assertion:{val.command_or_assertion}", authorized_tokens=None, scoped_paths=(rel_path,))

        val_tokens = normalize_command_to_tokens(val.command_or_assertion)
        if val_tokens is not None:
            configured = [s.tokens for s in catalog.configured_tests]
            if match_command_exact(val_tokens, configured):
                return ValidationGrounding(validation_id=val.id, source_type="configured_test", source_reference="configured_test", authorized_tokens=val_tokens, scoped_paths=())
            recorded = [s.tokens for s in catalog.recorded_tests]
            if match_command_exact(val_tokens, recorded):
                return ValidationGrounding(validation_id=val.id, source_type="recorded_test", source_reference="recorded_test", authorized_tokens=val_tokens, scoped_paths=())

        raise GroundingError("validation_not_grounded", f"validation {val.id} cannot be grounded: {val.command_or_assertion!r}")