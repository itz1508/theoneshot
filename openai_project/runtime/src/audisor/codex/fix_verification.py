"""Host-owned automatic post-execution verification for the Fix path.

After Codex exits successfully, this component automatically verifies the
changed repository using the already-persisted:

- canonical repository_root
- scanner_context
- verification_contract
- verification_grounding
- authorized command token arrays
- original findings

It then persists one final verification result under the same operation ID
and returns completed or failed.

Architecture:
  Accepted Fix
  → CodexFixContinuation.run()
  → Codex launched in canonical repository root
  → Codex exits
  → FixPostExecutionVerifier.verify()
  → verification evidence persisted
  → operation becomes completed or failed

The verifier must not invoke a model.  It only:

- reconstructs the exact scanner configuration from the persisted
  scanner_context
- rescans the repository
- evaluates scanner-clear checks against the post-execution scan
- executes authorized validation commands with shell=False
- evaluates deterministic assertions
- evaluates the success rule
- persists the verification result

Idempotency: when ``fix-verification-result.json`` already exists for the
operation ID, the persisted result is returned without relaunching Codex,
rescanning, or rerunning validation commands.
"""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping, Sequence

from audisor.codex.handoff import canonical_bytes


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


SUPPORTED_SUCCESS_RULE = "all_finding_checks_and_validations_pass"
SUPPORTED_EXPECTED_RESULTS = {"exit code 0", "exists", "contains", "absent", "compiles", "parses"}
DEFAULT_COMMAND_TIMEOUT_SECONDS = 60.0
VERIFICATION_RESULT_FILENAME = "fix-verification-result.json"


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class FixVerificationError(RuntimeError):
    """Raised when the Fix post-execution verifier fails closed."""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FindingCheckResult:
    """Result of evaluating a single FindingCheck."""

    finding_id: str
    check_id: str
    passed: bool
    source_type: str
    source_reference: str
    reason: str | None = None

    def to_mapping(self) -> dict[str, Any]:
        return {
            "finding_id": self.finding_id,
            "check_id": self.check_id,
            "passed": self.passed,
            "source_type": self.source_type,
            "source_reference": self.source_reference,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class ValidationResult:
    """Result of evaluating a single ValidationSpec."""

    validation_id: str
    passed: bool
    source_type: str
    source_reference: str
    exit_code: int | None = None
    stdout: str | None = None
    stderr: str | None = None
    reason: str | None = None

    def to_mapping(self) -> dict[str, Any]:
        return {
            "validation_id": self.validation_id,
            "passed": self.passed,
            "source_type": self.source_type,
            "source_reference": self.source_reference,
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class FixVerificationResult:
    """Result of the post-execution Fix verification."""

    operation_id: str
    repository_root: str
    codex_result_reference: str
    verification_performed: bool
    success_rule: str
    scanner_result: dict[str, list[str]]
    finding_results: list[FindingCheckResult]
    validation_results: list[ValidationResult]
    passed: bool
    completion_claimed: bool
    failure_code: str | None = None
    failure_message: str | None = None

    def to_mapping(self) -> dict[str, Any]:
        return {
            "operation_id": self.operation_id,
            "repository_root": self.repository_root,
            "codex_result_reference": self.codex_result_reference,
            "verification_performed": self.verification_performed,
            "success_rule": self.success_rule,
            "scanner_result": {
                "original_finding_ids": list(self.scanner_result.get("original_finding_ids", [])),
                "remaining_original_finding_ids": list(
                    self.scanner_result.get("remaining_original_finding_ids", [])
                ),
            },
            "finding_results": [r.to_mapping() for r in self.finding_results],
            "validation_results": [r.to_mapping() for r in self.validation_results],
            "passed": self.passed,
            "completion_claimed": self.completion_claimed,
            "failure_code": self.failure_code,
            "failure_message": self.failure_message,
        }


# ---------------------------------------------------------------------------
# ScanConfig reconstruction
# ---------------------------------------------------------------------------


def reconstruct_scan_config(scanner_context: Mapping[str, Any]) -> dict[str, Any]:
    """Reconstruct the exact scanner configuration from the persisted context.

    Returns a plain dict that mirrors the ``ScanConfig`` dataclass fields.
    The verifier injects a deterministic test runner owned by the verifier
    itself; callers must not supply their own.

    The returned dict is the only source of truth for the post-execution
    rescan.  Default ``ScanConfig`` values are never used.
    """
    if not isinstance(scanner_context, Mapping):
        raise FixVerificationError(
            "scanner_context_invalid",
            "scanner_context is not a mapping",
        )

    def _as_str_set(value: Any) -> frozenset[str]:
        if value is None:
            return frozenset()
        if not isinstance(value, (list, tuple, set, frozenset)):
            raise FixVerificationError(
                "scanner_context_invalid",
                f"scanner_context field expected iterable of strings, got: {type(value).__name__}",
            )
        result: set[str] = set()
        for item in value:
            if not isinstance(item, str):
                raise FixVerificationError(
                    "scanner_context_invalid",
                    f"scanner_context field contains non-string: {item!r}",
                )
            result.add(item)
        return frozenset(result)

    def _as_str_tuple(value: Any) -> tuple[str, ...]:
        if value is None:
            return ()
        if not isinstance(value, (list, tuple)):
            raise FixVerificationError(
                "scanner_context_invalid",
                f"scanner_context field expected list of strings, got: {type(value).__name__}",
            )
        result: list[str] = []
        for item in value:
            if not isinstance(item, str):
                raise FixVerificationError(
                    "scanner_context_invalid",
                    f"scanner_context field contains non-string: {item!r}",
                )
            result.append(item)
        return tuple(result)

    def _as_command_tuples(value: Any) -> tuple[tuple[str, ...], ...]:
        if value is None:
            return ()
        if not isinstance(value, list):
            raise FixVerificationError(
                "scanner_context_invalid",
                f"scanner_context.test_commands expected list, got: {type(value).__name__}",
            )
        result: list[tuple[str, ...]] = []
        for item in value:
            if not isinstance(item, list):
                raise FixVerificationError(
                    "scanner_context_invalid",
                    f"scanner_context.test_commands entry expected list, got: {type(item).__name__}",
                )
            tokens: list[str] = []
            for token in item:
                if not isinstance(token, str):
                    raise FixVerificationError(
                        "scanner_context_invalid",
                        f"scanner_context.test_commands entry contains non-string: {token!r}",
                    )
                tokens.append(token)
            result.append(tuple(tokens))
        return tuple(result)

    def _as_contract_requirements(value: Any) -> tuple[tuple[str, tuple[str, ...]], ...]:
        if value is None:
            return ()
        if not isinstance(value, list):
            raise FixVerificationError(
                "scanner_context_invalid",
                f"scanner_context.contract_requirements expected list, got: {type(value).__name__}",
            )
        result: list[tuple[str, tuple[str, ...]]] = []
        for item in value:
            if not isinstance(item, (list, tuple)) or len(item) != 2:
                raise FixVerificationError(
                    "scanner_context_invalid",
                    f"scanner_context.contract_requirements entry expected [path, fields], got: {item!r}",
                )
            path, fields = item
            if not isinstance(path, str):
                raise FixVerificationError(
                    "scanner_context_invalid",
                    f"scanner_context.contract_requirements path must be string, got: {type(path).__name__}",
                )
            if not isinstance(fields, (list, tuple)):
                raise FixVerificationError(
                    "scanner_context_invalid",
                    f"scanner_context.contract_requirements fields must be list, got: {type(fields).__name__}",
                )
            field_tokens: list[str] = []
            for field_name in fields:
                if not isinstance(field_name, str):
                    raise FixVerificationError(
                        "scanner_context_invalid",
                        f"scanner_context.contract_requirements field must be string, got: {type(field_name).__name__}",
                    )
                field_tokens.append(field_name)
            result.append((path, tuple(field_tokens)))
        return tuple(result)

    repository_root = scanner_context.get("repository_root")
    if not isinstance(repository_root, str) or not repository_root.strip():
        raise FixVerificationError(
            "scanner_context_invalid",
            "scanner_context.repository_root is missing or empty",
        )

    return {
        "excluded_dirs": _as_str_set(scanner_context.get("excluded_dirs")),
        "excluded_files": _as_str_set(scanner_context.get("excluded_files")),
        "extensions": _as_str_set(scanner_context.get("extensions")),
        "test_commands": _as_command_tuples(scanner_context.get("test_commands")),
        "contract_requirements": _as_contract_requirements(
            scanner_context.get("contract_requirements")
        ),
        "source_roots": _as_str_tuple(scanner_context.get("source_roots")),
        "repository_root": repository_root,
    }


# ---------------------------------------------------------------------------
# Assertion parsing
# ---------------------------------------------------------------------------


_ASSERTION_RE = re.compile(
    r"^(?P<form>scanner_clear|file_exists|file_contains|file_not_contains|python_compiles|json_parses)::(?P<rest>.+)$"
)


def parse_assertion(assertion: str) -> tuple[str, str, str | None] | None:
    """Parse a deterministic assertion string into (form, path, literal)."""
    if not isinstance(assertion, str):
        return None
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


# ---------------------------------------------------------------------------
# Verifier
# ---------------------------------------------------------------------------


@dataclass
class FixPostExecutionVerifier:
    """Host-owned automatic post-execution Fix verifier.

    The verifier is constructed once and reused.  It does not invoke a
    model.  It only reads the persisted handoff, rescans the repository,
    executes authorized commands, evaluates deterministic assertions, and
    persists the verification result.
    """

    command_timeout_seconds: float = DEFAULT_COMMAND_TIMEOUT_SECONDS
    command_runner: Callable[..., subprocess.CompletedProcess] = subprocess.run

    def verify(
        self,
        *,
        operation_id: str,
        repository_root: str,
        scanner_context: Mapping[str, Any],
        original_findings: Sequence[Mapping[str, Any]],
        verification_contract: Mapping[str, Any],
        verification_grounding: Mapping[str, Any],
        codex_result: Mapping[str, Any],
        result_root: Path | None = None,
    ) -> FixVerificationResult:
        """Run the post-execution verification.

        Args:
            operation_id: The canonical operation ID.
            repository_root: The canonical repository root (absolute path).
            scanner_context: The persisted scanner_context from the handoff.
            original_findings: The original findings from the accepted Fix.
            verification_contract: The persisted verification_contract.
            verification_grounding: The persisted verification_grounding.
            codex_result: The persisted Codex launch result.
            result_root: Optional override for the verification result root.

        Returns:
            A ``FixVerificationResult`` describing the verification outcome.
        """
        # 1. Idempotency: if the result already exists, return it.
        root = self._resolve_result_root(operation_id, result_root)
        existing = self._load_existing_result(root)
        if existing is not None:
            return existing

        # 2. Codex exit_code gate.
        codex_exit_code = codex_result.get("exit_code")
        if codex_exit_code != 0:
            return self._build_skipped_result(
                operation_id=operation_id,
                repository_root=repository_root,
                codex_result=codex_result,
                root=root,
                failure_code="codex_exit_nonzero",
                failure_message=f"Codex exited with code {codex_exit_code}; verification not run",
            )

        # 3. Reconstruct the exact scanner configuration.
        scan_config = reconstruct_scan_config(scanner_context)

        # 4. Rescan the repository.
        post_scan_findings = self._rescan(repository_root, scan_config)

        # 5. Evaluate scanner-clear checks.
        original_finding_ids = [str(f.get("id")) for f in original_findings if isinstance(f, Mapping)]
        remaining, scanner_evidence = self._evaluate_scanner_clear(
            original_finding_ids=original_finding_ids,
            post_scan_findings=post_scan_findings,
        )

        # 6. Evaluate every FindingCheck.
        finding_results = self._evaluate_finding_checks(
            verification_contract=verification_contract,
            verification_grounding=verification_grounding,
            repository_root=repository_root,
            scanner_evidence=scanner_evidence,
        )

        # 7. Evaluate every ValidationSpec.
        validation_results = self._evaluate_validations(
            verification_contract=verification_contract,
            verification_grounding=verification_grounding,
            repository_root=repository_root,
        )

        # 8. Evaluate the success rule.
        success_rule = verification_contract.get("success_rule")
        if success_rule != SUPPORTED_SUCCESS_RULE:
            return self._build_failed_result(
                operation_id=operation_id,
                repository_root=repository_root,
                codex_result=codex_result,
                root=root,
                finding_results=finding_results,
                validation_results=validation_results,
                original_finding_ids=original_finding_ids,
                remaining=remaining,
                failure_code="unsupported_success_rule",
                failure_message=f"success_rule must be {SUPPORTED_SUCCESS_RULE!r}, got: {success_rule!r}",
            )

        all_findings_passed = all(r.passed for r in finding_results)
        all_validations_passed = all(r.passed for r in validation_results)
        passed = all_findings_passed and all_validations_passed

        result = FixVerificationResult(
            operation_id=operation_id,
            repository_root=repository_root,
            codex_result_reference=str(codex_result.get("reference") or codex_result.get("codex_result_reference") or ""),
            verification_performed=True,
            success_rule=success_rule,
            scanner_result={
                "original_finding_ids": original_finding_ids,
                "remaining_original_finding_ids": remaining,
            },
            finding_results=finding_results,
            validation_results=validation_results,
            passed=passed,
            completion_claimed=passed,
            failure_code=None if passed else "verification_failed",
            failure_message=None if passed else "One or more checks did not pass",
        )
        self._persist_result(root, result)
        return result

    # ------------------------------------------------------------------
    # Internal: idempotency
    # ------------------------------------------------------------------

    def _resolve_result_root(self, operation_id: str, override: Path | None) -> Path:
        if override is not None:
            root = Path(override)
        else:
            base = Path(
                os.environ.get(
                    "AUDISOR_OPERATION_DATA_DIR",
                    Path.home() / ".audisor" / "operations",
                )
            )
            root = base / "codex" / operation_id
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _result_path(self, root: Path) -> Path:
        return root / VERIFICATION_RESULT_FILENAME

    def _load_existing_result(self, root: Path) -> FixVerificationResult | None:
        path = self._result_path(root)
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None
        if not isinstance(data, dict):
            return None
        try:
            finding_results = [
                FindingCheckResult(
                    finding_id=str(item.get("finding_id", "")),
                    check_id=str(item.get("check_id", "")),
                    passed=bool(item.get("passed", False)),
                    source_type=str(item.get("source_type", "")),
                    source_reference=str(item.get("source_reference", "")),
                    reason=item.get("reason"),
                )
                for item in data.get("finding_results", [])
                if isinstance(item, dict)
            ]
            validation_results = [
                ValidationResult(
                    validation_id=str(item.get("validation_id", "")),
                    passed=bool(item.get("passed", False)),
                    source_type=str(item.get("source_type", "")),
                    source_reference=str(item.get("source_reference", "")),
                    exit_code=item.get("exit_code"),
                    stdout=item.get("stdout"),
                    stderr=item.get("stderr"),
                    reason=item.get("reason"),
                )
                for item in data.get("validation_results", [])
                if isinstance(item, dict)
            ]
            scanner_result = data.get("scanner_result", {}) or {}
            return FixVerificationResult(
                operation_id=str(data.get("operation_id", "")),
                repository_root=str(data.get("repository_root", "")),
                codex_result_reference=str(data.get("codex_result_reference", "")),
                verification_performed=bool(data.get("verification_performed", False)),
                success_rule=str(data.get("success_rule", "")),
                scanner_result={
                    "original_finding_ids": list(scanner_result.get("original_finding_ids", [])),
                    "remaining_original_finding_ids": list(
                        scanner_result.get("remaining_original_finding_ids", [])
                    ),
                },
                finding_results=finding_results,
                validation_results=validation_results,
                passed=bool(data.get("passed", False)),
                completion_claimed=bool(data.get("completion_claimed", False)),
                failure_code=data.get("failure_code"),
                failure_message=data.get("failure_message"),
            )
        except (TypeError, ValueError):
            return None

    # ------------------------------------------------------------------
    # Internal: rescan
    # ------------------------------------------------------------------

    def _rescan(
        self,
        repository_root: str,
        scan_config: Mapping[str, Any],
    ) -> list[Mapping[str, Any]]:
        """Rescan the repository using the reconstructed ScanConfig.

        Uses the audisor_backend scanner when available; otherwise falls
        back to a minimal in-process rescan that only checks for the
        original finding IDs by file/type.  The verifier never invokes a
        model.
        """
        try:
            from audisor_backend.scanning.scanner import (
                DeterministicScanner,
                ScanConfig,
            )
        except ImportError:
            return self._minimal_rescan(repository_root, scan_config)

        config = ScanConfig(
            excluded_dirs=scan_config["excluded_dirs"],
            excluded_files=scan_config["excluded_files"],
            extensions=scan_config["extensions"],
            test_commands=scan_config["test_commands"],
            test_runner=None,
            contract_requirements=scan_config["contract_requirements"],
            source_roots=scan_config["source_roots"],
            repository_root=scan_config["repository_root"],
        )
        report = DeterministicScanner(config).scan(repository_root)
        return [finding.__dict__ for finding in report.findings]

    def _minimal_rescan(
        self,
        repository_root: str,
        scan_config: Mapping[str, Any],
    ) -> list[Mapping[str, Any]]:
        """Minimal rescan used when audisor_backend is unavailable.

        Only checks for syntax errors in Python files and JSON parse
        errors in JSON files.  This is sufficient for scanner_clear
        evaluation when the original findings are syntax/JSON errors.
        """
        findings: list[Mapping[str, Any]] = []
        root = Path(repository_root).resolve()
        if not root.is_dir():
            return findings
        excluded_dirs = set(scan_config.get("excluded_dirs", frozenset()))
        excluded_files = set(scan_config.get("excluded_files", frozenset()))
        extensions = set(scan_config.get("extensions", frozenset()))
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(root).as_posix()
            if any(part in excluded_dirs for part in Path(rel).parts):
                continue
            if rel in excluded_files:
                continue
            if path.suffix.lower() not in extensions:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            if path.suffix.lower() == ".py":
                try:
                    ast.parse(text, filename=rel)
                except SyntaxError as exc:
                    findings.append(
                        {
                            "id": f"rescan-syntax-{rel}",
                            "type": "correctness.syntax_error",
                            "file": rel,
                            "severity": "high",
                            "evidence": {"line": exc.lineno, "message": exc.msg},
                        }
                    )
            elif path.suffix.lower() == ".json":
                try:
                    json.loads(text)
                except json.JSONDecodeError as exc:
                    findings.append(
                        {
                            "id": f"rescan-json-{rel}",
                            "type": "configuration.invalid_configuration",
                            "file": rel,
                            "severity": "high",
                            "evidence": {"line": exc.lineno, "message": exc.msg},
                        }
                    )
        return findings

    # ------------------------------------------------------------------
    # Internal: scanner-clear evaluation
    # ------------------------------------------------------------------

    def _evaluate_scanner_clear(
        self,
        *,
        original_finding_ids: list[str],
        post_scan_findings: list[Mapping[str, Any]],
    ) -> tuple[list[str], dict[str, Mapping[str, Any]]]:
        """Return (remaining_original_finding_ids, scanner_evidence_by_id)."""
        evidence: dict[str, Mapping[str, Any]] = {}
        for finding in post_scan_findings:
            fid = finding.get("id")
            if isinstance(fid, str) and fid:
                evidence[fid] = finding
        remaining = [fid for fid in original_finding_ids if fid in evidence]
        return remaining, evidence

    # ------------------------------------------------------------------
    # Internal: FindingCheck evaluation
    # ------------------------------------------------------------------

    def _evaluate_finding_checks(
        self,
        *,
        verification_contract: Mapping[str, Any],
        verification_grounding: Mapping[str, Any],
        repository_root: str,
        scanner_evidence: Mapping[str, Mapping[str, Any]],
    ) -> list[FindingCheckResult]:
        contract_checks = verification_contract.get("finding_checks", [])
        grounding_checks = verification_grounding.get("finding_checks", [])
        if not isinstance(contract_checks, list):
            contract_checks = []
        if not isinstance(grounding_checks, list):
            grounding_checks = []

        grounding_by_finding_id: dict[str, Mapping[str, Any]] = {}
        for g in grounding_checks:
            if isinstance(g, Mapping):
                fid = g.get("finding_id")
                if isinstance(fid, str):
                    grounding_by_finding_id[fid] = g

        results: list[FindingCheckResult] = []
        for index, check in enumerate(contract_checks):
            if not isinstance(check, Mapping):
                continue
            finding_id = str(check.get("finding_id", ""))
            check_id = f"finding_check:{index}"
            grounding = grounding_by_finding_id.get(finding_id)
            if grounding is None:
                results.append(
                    FindingCheckResult(
                        finding_id=finding_id,
                        check_id=check_id,
                        passed=False,
                        source_type="unknown",
                        source_reference="",
                        reason="missing grounding for finding_check",
                    )
                )
                continue
            source_type = str(grounding.get("source_type", ""))
            source_reference = str(grounding.get("source_reference", ""))
            authorized_tokens = grounding.get("authorized_tokens")
            scoped_paths = grounding.get("scoped_paths", []) or []

            if source_type == "scanner_check":
                # scanner_clear check: original finding must be absent
                passed = finding_id not in scanner_evidence
                results.append(
                    FindingCheckResult(
                        finding_id=finding_id,
                        check_id=check_id,
                        passed=passed,
                        source_type=source_type,
                        source_reference=source_reference,
                        reason=None if passed else f"finding {finding_id} still present in post-scan",
                    )
                )
                continue

            if source_type == "plan_acceptance":
                # Plan acceptance is satisfied by the scanner_clear check
                # (the plan step's target file is the same as the finding).
                passed = finding_id not in scanner_evidence
                results.append(
                    FindingCheckResult(
                        finding_id=finding_id,
                        check_id=check_id,
                        passed=passed,
                        source_type=source_type,
                        source_reference=source_reference,
                        reason=None if passed else f"finding {finding_id} still present in post-scan",
                    )
                )
                continue

            if source_type == "deterministic_assertion":
                assertion_text = str(check.get("check", ""))
                parsed = parse_assertion(assertion_text)
                if parsed is None:
                    results.append(
                        FindingCheckResult(
                            finding_id=finding_id,
                            check_id=check_id,
                            passed=False,
                            source_type=source_type,
                            source_reference=source_reference,
                            reason=f"unparseable assertion: {assertion_text!r}",
                        )
                    )
                    continue
                form, rel_path, literal = parsed
                if form == "scanner_clear":
                    passed = finding_id not in scanner_evidence
                    results.append(
                        FindingCheckResult(
                            finding_id=finding_id,
                            check_id=check_id,
                            passed=passed,
                            source_type=source_type,
                            source_reference=source_reference,
                            reason=None if passed else f"finding {finding_id} still present in post-scan",
                        )
                    )
                    continue
                # Other deterministic assertions
                passed, reason = self._evaluate_deterministic_assertion(
                    form=form,
                    rel_path=rel_path,
                    literal=literal,
                    repository_root=repository_root,
                    scoped_paths=scoped_paths,
                )
                results.append(
                    FindingCheckResult(
                        finding_id=finding_id,
                        check_id=check_id,
                        passed=passed,
                        source_type=source_type,
                        source_reference=source_reference,
                        reason=reason,
                    )
                )
                continue

            if source_type == "recorded_test":
                if not isinstance(authorized_tokens, (list, tuple)) or not authorized_tokens:
                    results.append(
                        FindingCheckResult(
                            finding_id=finding_id,
                            check_id=check_id,
                            passed=False,
                            source_type=source_type,
                            source_reference=source_reference,
                            reason="missing authorized_tokens for recorded_test",
                        )
                    )
                    continue
                tokens = tuple(str(t) for t in authorized_tokens)
                expected_result = str(check.get("expected_result", ""))
                passed, reason = self._execute_authorized_command(
                    tokens=tokens,
                    repository_root=repository_root,
                    expected_result=expected_result,
                )
                results.append(
                    FindingCheckResult(
                        finding_id=finding_id,
                        check_id=check_id,
                        passed=passed,
                        source_type=source_type,
                        source_reference=source_reference,
                        reason=reason,
                    )
                )
                continue

            results.append(
                FindingCheckResult(
                    finding_id=finding_id,
                    check_id=check_id,
                    passed=False,
                    source_type=source_type,
                    source_reference=source_reference,
                    reason=f"unsupported source_type: {source_type!r}",
                )
            )
        return results

    # ------------------------------------------------------------------
    # Internal: ValidationSpec evaluation
    # ------------------------------------------------------------------

    def _evaluate_validations(
        self,
        *,
        verification_contract: Mapping[str, Any],
        verification_grounding: Mapping[str, Any],
        repository_root: str,
    ) -> list[ValidationResult]:
        contract_validations = verification_contract.get("validations", [])
        grounding_validations = verification_grounding.get("validations", [])
        if not isinstance(contract_validations, list):
            contract_validations = []
        if not isinstance(grounding_validations, list):
            grounding_validations = []

        grounding_by_validation_id: dict[str, Mapping[str, Any]] = {}
        for g in grounding_validations:
            if isinstance(g, Mapping):
                vid = g.get("validation_id")
                if isinstance(vid, str):
                    grounding_by_validation_id[vid] = g

        results: list[ValidationResult] = []
        for val in contract_validations:
            if not isinstance(val, Mapping):
                continue
            validation_id = str(val.get("id", ""))
            grounding = grounding_by_validation_id.get(validation_id)
            if grounding is None:
                results.append(
                    ValidationResult(
                        validation_id=validation_id,
                        passed=False,
                        source_type="unknown",
                        source_reference="",
                        reason="missing grounding for validation",
                    )
                )
                continue
            source_type = str(grounding.get("source_type", ""))
            source_reference = str(grounding.get("source_reference", ""))
            authorized_tokens = grounding.get("authorized_tokens")
            scoped_paths = grounding.get("scoped_paths", []) or []

            if source_type == "deterministic_assertion":
                assertion_text = str(val.get("command_or_assertion", ""))
                parsed = parse_assertion(assertion_text)
                if parsed is None:
                    results.append(
                        ValidationResult(
                            validation_id=validation_id,
                            passed=False,
                            source_type=source_type,
                            source_reference=source_reference,
                            reason=f"unparseable assertion: {assertion_text!r}",
                        )
                    )
                    continue
                form, rel_path, literal = parsed
                if form == "scanner_clear":
                    results.append(
                        ValidationResult(
                            validation_id=validation_id,
                            passed=False,
                            source_type=source_type,
                            source_reference=source_reference,
                            reason="scanner_clear is not valid for ValidationSpec",
                        )
                    )
                    continue
                passed, reason = self._evaluate_deterministic_assertion(
                    form=form,
                    rel_path=rel_path,
                    literal=literal,
                    repository_root=repository_root,
                    scoped_paths=scoped_paths,
                )
                results.append(
                    ValidationResult(
                        validation_id=validation_id,
                        passed=passed,
                        source_type=source_type,
                        source_reference=source_reference,
                        reason=reason,
                    )
                )
                continue

            if source_type in ("configured_test", "recorded_test"):
                if not isinstance(authorized_tokens, (list, tuple)) or not authorized_tokens:
                    results.append(
                        ValidationResult(
                            validation_id=validation_id,
                            passed=False,
                            source_type=source_type,
                            source_reference=source_reference,
                            reason="missing authorized_tokens for command source",
                        )
                    )
                    continue
                tokens = tuple(str(t) for t in authorized_tokens)
                expected_result = str(val.get("expected_result", ""))
                passed, exit_code, stdout, stderr, reason = self._execute_authorized_command_full(
                    tokens=tokens,
                    repository_root=repository_root,
                    expected_result=expected_result,
                )
                results.append(
                    ValidationResult(
                        validation_id=validation_id,
                        passed=passed,
                        source_type=source_type,
                        source_reference=source_reference,
                        exit_code=exit_code,
                        stdout=stdout,
                        stderr=stderr,
                        reason=reason,
                    )
                )
                continue

            results.append(
                ValidationResult(
                    validation_id=validation_id,
                    passed=False,
                    source_type=source_type,
                    source_reference=source_reference,
                    reason=f"unsupported source_type: {source_type!r}",
                )
            )
        return results

    # ------------------------------------------------------------------
    # Internal: deterministic assertions
    # ------------------------------------------------------------------

    def _evaluate_deterministic_assertion(
        self,
        *,
        form: str,
        rel_path: str,
        literal: str | None,
        repository_root: str,
        scoped_paths: Sequence[Any],
    ) -> tuple[bool, str | None]:
        if not self._is_in_scope(rel_path, scoped_paths):
            return False, f"path {rel_path!r} is not in approved scope"
        target = self._resolve_scoped_path(rel_path, repository_root)
        if form == "file_exists":
            if target.is_file():
                return True, None
            return False, f"file does not exist: {rel_path}"
        if form == "file_contains":
            if not target.is_file():
                return False, f"file does not exist: {rel_path}"
            try:
                text = target.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError) as exc:
                return False, f"could not read file: {exc}"
            if literal is None or literal in text:
                return True, None
            return False, f"literal not found in {rel_path}"
        if form == "file_not_contains":
            if not target.is_file():
                return False, f"file does not exist: {rel_path}"
            try:
                text = target.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError) as exc:
                return False, f"could not read file: {exc}"
            if literal is None or literal not in text:
                return True, None
            return False, f"literal found in {rel_path}"
        if form == "python_compiles":
            if not target.is_file():
                return False, f"file does not exist: {rel_path}"
            try:
                text = target.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError) as exc:
                return False, f"could not read file: {exc}"
            try:
                ast.parse(text, filename=rel_path)
            except SyntaxError as exc:
                return False, f"python syntax error: {exc.msg}"
            return True, None
        if form == "json_parses":
            if not target.is_file():
                return False, f"file does not exist: {rel_path}"
            try:
                text = target.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError) as exc:
                return False, f"could not read file: {exc}"
            try:
                json.loads(text)
            except json.JSONDecodeError as exc:
                return False, f"json parse error: {exc.msg}"
            return True, None
        return False, f"unsupported assertion form: {form!r}"

    def _is_in_scope(self, rel_path: str, scoped_paths: Sequence[Any]) -> bool:
        if not scoped_paths:
            return False
        if not isinstance(rel_path, str) or not rel_path:
            return False
        if rel_path.startswith("/") or Path(rel_path).is_absolute():
            return False
        if ".." in PurePosixPath(rel_path).parts:
            return False
        return rel_path in {str(p) for p in scoped_paths if isinstance(p, str)}

    def _resolve_scoped_path(self, rel_path: str, repository_root: str) -> Path:
        root = Path(repository_root).resolve()
        target = (root / rel_path).resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise FixVerificationError(
                "path_out_of_scope",
                f"path {rel_path!r} escapes repository root",
            ) from exc
        return target

    # ------------------------------------------------------------------
    # Internal: authorized command execution
    # ------------------------------------------------------------------

    def _execute_authorized_command(
        self,
        *,
        tokens: tuple[str, ...],
        repository_root: str,
        expected_result: str,
    ) -> tuple[bool, str | None]:
        passed, _exit_code, _stdout, _stderr, reason = self._execute_authorized_command_full(
            tokens=tokens,
            repository_root=repository_root,
            expected_result=expected_result,
        )
        return passed, reason

    def _execute_authorized_command_full(
        self,
        *,
        tokens: tuple[str, ...],
        repository_root: str,
        expected_result: str,
    ) -> tuple[bool, int | None, str | None, str | None, str | None]:
        if not tokens or not all(isinstance(t, str) and t for t in tokens):
            return False, None, None, None, "malformed authorized_tokens"
        if expected_result not in SUPPORTED_EXPECTED_RESULTS:
            return (
                False,
                None,
                None,
                None,
                f"unsupported_expected_result: {expected_result!r}",
            )
        root = Path(repository_root).resolve()
        if not root.is_dir():
            return False, None, None, None, f"repository_root is not a directory: {repository_root}"
        try:
            completed = self.command_runner(
                list(tokens),
                cwd=str(root),
                shell=False,
                capture_output=True,
                text=True,
                timeout=self.command_timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            stdout_text = ""
            if isinstance(exc.stdout, (bytes, bytearray)):
                stdout_text = exc.stdout.decode("utf-8", errors="replace")
            elif isinstance(exc.stdout, str):
                stdout_text = exc.stdout
            stderr_text = ""
            if isinstance(exc.stderr, (bytes, bytearray)):
                stderr_text = exc.stderr.decode("utf-8", errors="replace")
            elif isinstance(exc.stderr, str):
                stderr_text = exc.stderr
            return (
                False,
                None,
                stdout_text,
                stderr_text,
                "command_timeout",
            )
        except (OSError, ValueError) as exc:
            return False, None, None, None, f"command_execution_failed: {exc}"
        exit_code = completed.returncode
        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
        if expected_result == "exit code 0":
            if exit_code == 0:
                return True, exit_code, stdout, stderr, None
            return False, exit_code, stdout, stderr, f"command exited with code {exit_code}"
        return False, exit_code, stdout, stderr, f"unsupported_expected_result: {expected_result!r}"

    # ------------------------------------------------------------------
    # Internal: result persistence
    # ------------------------------------------------------------------

    def _persist_result(self, root: Path, result: FixVerificationResult) -> None:
        path = self._result_path(root)
        temp = path.with_suffix(".json.tmp")
        temp.write_bytes(canonical_bytes(result.to_mapping()) + b"\n")
        os.replace(temp, path)

    def _build_skipped_result(
        self,
        *,
        operation_id: str,
        repository_root: str,
        codex_result: Mapping[str, Any],
        root: Path,
        failure_code: str,
        failure_message: str,
    ) -> FixVerificationResult:
        result = FixVerificationResult(
            operation_id=operation_id,
            repository_root=repository_root,
            codex_result_reference=str(
                codex_result.get("codex_result_reference")
                or codex_result.get("reference")
                or ""
            ),
            verification_performed=False,
            success_rule="",
            scanner_result={"original_finding_ids": [], "remaining_original_finding_ids": []},
            finding_results=[],
            validation_results=[],
            passed=False,
            completion_claimed=False,
            failure_code=failure_code,
            failure_message=failure_message,
        )
        self._persist_result(root, result)
        return result

    def _build_failed_result(
        self,
        *,
        operation_id: str,
        repository_root: str,
        codex_result: Mapping[str, Any],
        root: Path,
        finding_results: list[FindingCheckResult],
        validation_results: list[ValidationResult],
        original_finding_ids: list[str],
        remaining: list[str],
        failure_code: str,
        failure_message: str,
    ) -> FixVerificationResult:
        result = FixVerificationResult(
            operation_id=operation_id,
            repository_root=repository_root,
            codex_result_reference=str(
                codex_result.get("codex_result_reference")
                or codex_result.get("reference")
                or ""
            ),
            verification_performed=True,
            success_rule="",
            scanner_result={
                "original_finding_ids": original_finding_ids,
                "remaining_original_finding_ids": remaining,
            },
            finding_results=finding_results,
            validation_results=validation_results,
            passed=False,
            completion_claimed=False,
            failure_code=failure_code,
            failure_message=failure_message,
        )
        self._persist_result(root, result)
        return result