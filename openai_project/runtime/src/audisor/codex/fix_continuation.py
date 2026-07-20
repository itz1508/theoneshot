"""Host-owned automatic Fix-to-Codex continuation boundary.

After the existing Fix dispatcher accepts a Fix and persists
``qualified-fix-handoff.json``, this component automatically passes that
handoff to Codex under the same operation ID.  This is automatic host
continuation — no second CLI command is required.

Architecture:
  AudisorOperationExecutor.execute()
  → _execute_fix()
  → AcceptedFixDispatcher.dispatch()
  → accepted Fix handoff persisted
  → CodexFixContinuation.run()
  → Codex launched once
  → launch result persisted under the same operation ID

The original ``qualified-fix-handoff.json`` is never modified.  Its authority
fields remain ``false`` because ``audisor_backend`` does not grant execution
authority.  A separate host-owned Codex launch envelope carries bounded
execution authority separately.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping, Sequence

from audisor.codex.fix_verification import (
    FixPostExecutionVerifier,
    FixVerificationResult,
)
from audisor.codex.handoff import canonical_bytes, persist_launch_result
from audisor.codex.launcher import CodexLaunchError, launch_codex
from audisor.security.path_security import check_paths_allowed


class FixContinuationError(RuntimeError):
    """Raised when the Fix-to-Codex continuation boundary fails."""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code


@dataclass(frozen=True)
class FixContinuationResult:
    """Result of a Fix-to-Codex continuation launch and verification."""

    operation_id: str
    handoff_path: str
    codex_envelope_path: str
    codex_result_reference: str
    pid: int | None
    exit_code: int
    outcome: str
    resolved_command: tuple[str, ...]
    working_directory: str
    verification_result_reference: str | None = None
    verification_performed: bool = False
    verification_passed: bool = False
    completion_claimed: bool = False

    def to_mapping(self) -> dict[str, Any]:
        return {
            "operation_id": self.operation_id,
            "handoff_path": self.handoff_path,
            "codex_envelope_path": self.codex_envelope_path,
            "codex_result_reference": self.codex_result_reference,
            "pid": self.pid,
            "exit_code": self.exit_code,
            "outcome": self.outcome,
            "resolved_command": list(self.resolved_command),
            "working_directory": self.working_directory,
            "verification_result_reference": self.verification_result_reference,
            "verification_performed": self.verification_performed,
            "verification_passed": self.verification_passed,
            "completion_claimed": self.completion_claimed,
        }


class CodexFixContinuation:
    """Automatic host-owned Fix-to-Codex continuation.

    Receives an accepted Fix handoff and launches Codex once under the same
    operation ID.  The original handoff is never modified; a separate
    host-owned Codex launch envelope carries bounded execution authority.
    """

    def __init__(
        self,
        *,
        launcher: Callable[..., tuple[int | None, int, str, tuple[str, ...], str, str]] = launch_codex,
        launch_result_store_root: Path | None = None,
        verifier: FixPostExecutionVerifier | None = None,
    ) -> None:
        self._launcher = launcher
        self._launch_result_store_root = launch_result_store_root
        self._verifier = verifier

    def run(
        self,
        *,
        operation_id: str,
        handoff_path: str,
        receipt: Any,
        allowed_target_paths: Sequence[str],
        working_directory: Path | None = None,
        codex_envelope_root: Path | None = None,
    ) -> FixContinuationResult:
        """Launch Codex with the accepted Fix handoff.

        Validates the handoff, builds a host-owned Codex launch envelope,
        launches Codex once, and persists the launch result under the same
        operation ID.

        Args:
            operation_id: The canonical operation ID.
            handoff_path: Path to ``qualified-fix-handoff.json``.
            receipt: The existing ``MutationReceipt``.
            allowed_target_paths: Frozen allowed target paths from authority.
            working_directory: The Codex working directory (target root).
            codex_envelope_root: Root for envelope/result persistence.

        Returns:
            ``FixContinuationResult`` with launch details.

        Raises:
            ``FixContinuationError`` on validation or launch failure.
        """
        # 1. Validate the handoff exists and is well-formed
        handoff = self._load_and_validate_handoff(handoff_path, operation_id)

        # 2. Validate the verification contract is present and complete
        self._validate_verification_contract(handoff)

        # 3. Validate the verification grounding is present and complete
        self._validate_verification_grounding(handoff)

        # 4. Validate scanner_context and resolve canonical repository root
        repository_root = self._validate_scanner_context_and_root(handoff, allowed_target_paths)

        # 5. Validate plan targets are inside authorized paths
        self._validate_plan_targets(handoff, allowed_target_paths)

        # 5. Build the host-owned Codex launch envelope
        envelope_root = codex_envelope_root or self._default_envelope_root(operation_id)
        envelope = self._build_codex_envelope(
            operation_id=operation_id,
            handoff_path=handoff_path,
            handoff=handoff,
            receipt=receipt,
            allowed_target_paths=allowed_target_paths,
            repository_root=repository_root,
        )
        envelope_path = self._persist_envelope(envelope_root, envelope)

        # 6. Build Codex stdin from the envelope
        stdin_bytes = self._build_codex_stdin(envelope)

        # 7. Launch Codex once in the explicit repository root
        try:
            pid, exit_code, outcome, argv, stdout_text, stderr_text = self._launcher(
                stdin_bytes=stdin_bytes,
                cwd=Path(repository_root),
            )
        except CodexLaunchError as exc:
            # Persist the failure and re-raise as FixContinuationError
            failure_result = {
                "operation_id": operation_id,
                "outcome": "codex_failed",
                "failure_code": exc.code,
                "handoff_path": handoff_path,
                "codex_envelope_path": str(envelope_path),
                "codex_cwd": repository_root,
                "validation_cwd": repository_root,
                "stdout": "",
                "stderr": "",
                "completion_claimed": False,
                "verification_performed": False,
            }
            result_ref = self._persist_launch_result(envelope_root, failure_result)
            raise FixContinuationError(
                "codex_launch_failed",
                f"Codex launch failed: {exc}",
            ) from exc

        # 8. Persist the complete launch result under the same operation ID
        launch_result = {
            "operation_id": operation_id,
            "outcome": outcome,
            "pid": pid,
            "exit_code": exit_code,
            "stdout": stdout_text,
            "stderr": stderr_text,
            "codex_cwd": repository_root,
            "validation_cwd": repository_root,
            "handoff_path": handoff_path,
            "codex_envelope_path": str(envelope_path),
            "reference": str(envelope_root / "codex-result.json"),
            "completion_claimed": False,
            "verification_performed": False,
        }
        result_ref = self._persist_launch_result(envelope_root, launch_result)

        # 9. Run post-execution verification when Codex exited successfully.
        verification_result_reference: str | None = None
        verification_performed = False
        verification_passed = False
        completion_claimed = False
        if exit_code == 0 and self._verifier is not None:
            verification = self._verifier.verify(
                operation_id=operation_id,
                repository_root=repository_root,
                scanner_context=handoff.get("scanner_context", {}),
                original_findings=handoff.get("findings", []),
                verification_contract=handoff.get("verification_contract", {}),
                verification_grounding=handoff.get("verification_grounding", {}),
                codex_result=launch_result,
                result_root=envelope_root,
            )
            verification_result_reference = str(envelope_root / "fix-verification-result.json")
            verification_performed = verification.verification_performed
            verification_passed = verification.passed
            completion_claimed = verification.completion_claimed

        return FixContinuationResult(
            operation_id=operation_id,
            handoff_path=handoff_path,
            codex_envelope_path=str(envelope_path),
            codex_result_reference=result_ref,
            pid=pid,
            exit_code=exit_code,
            outcome=outcome,
            resolved_command=argv,
            working_directory=repository_root,
            verification_result_reference=verification_result_reference,
            verification_performed=verification_performed,
            verification_passed=verification_passed,
            completion_claimed=completion_claimed,
        )

    # ------------------------------------------------------------------
    # Internal: verification contract validation
    # ------------------------------------------------------------------

    def _validate_verification_contract(self, handoff: dict[str, Any]) -> None:
        """Validate the verification contract is present and complete.

        The host continuation must not rewrite, weaken, supplement, or
        reinterpret the verification contract.  It only validates that the
        contract exists and is structurally complete.
        """
        contract = handoff.get("verification_contract")
        if not isinstance(contract, dict):
            raise FixContinuationError(
                "verification_contract_incomplete",
                "Handoff does not contain a verification_contract",
            )

        # finding_checks must be a list (may be empty)
        finding_checks = contract.get("finding_checks")
        if not isinstance(finding_checks, list):
            raise FixContinuationError(
                "verification_contract_incomplete",
                "verification_contract.finding_checks must be a list",
            )

        # Every finding_check must reference an actual finding and be concrete
        finding_ids = {f.get("id") for f in handoff.get("findings", []) if isinstance(f, dict)}
        for check in finding_checks:
            if not isinstance(check, dict):
                raise FixContinuationError(
                    "verification_contract_incomplete",
                    "finding_check is not an object",
                )
            check_finding_id = check.get("finding_id")
            if check_finding_id not in finding_ids:
                raise FixContinuationError(
                    "verification_contract_incomplete",
                    f"finding_check references unknown finding_id: {check_finding_id}",
                )
            if check.get("resolution_method") not in ("rescan", "test", "assertion"):
                raise FixContinuationError(
                    "verification_contract_incomplete",
                    f"finding_check.resolution_method is invalid: {check.get('resolution_method')!r}",
                )
            if not isinstance(check.get("check"), str) or not check["check"].strip():
                raise FixContinuationError(
                    "verification_contract_incomplete",
                    "finding_check.check must be concrete and non-empty",
                )
            if not isinstance(check.get("expected_result"), str) or not check["expected_result"].strip():
                raise FixContinuationError(
                    "verification_contract_incomplete",
                    "finding_check.expected_result must be concrete and non-empty",
                )

        # Verify every finding is covered (only when finding_checks are present;
        # findings may also be covered by validations alone).
        if finding_checks:
            covered = {c["finding_id"] for c in finding_checks}
            if not covered >= finding_ids:
                missing = finding_ids - covered
                raise FixContinuationError(
                    "verification_contract_incomplete",
                    f"finding_checks do not cover every finding; missing: {missing}",
                )

        # validations must be a list with stable IDs and concrete commands
        validations = contract.get("validations")
        if not isinstance(validations, list):
            raise FixContinuationError(
                "verification_contract_incomplete",
                "verification_contract.validations is not a list",
            )
        seen_ids: set[str] = set()
        for val in validations:
            if not isinstance(val, dict):
                raise FixContinuationError(
                    "verification_contract_incomplete",
                    "validation is not an object",
                )
            vid = val.get("id")
            if not isinstance(vid, str) or not vid.strip():
                raise FixContinuationError(
                    "verification_contract_incomplete",
                    "validation.id must be a stable non-empty string",
                )
            if vid in seen_ids:
                raise FixContinuationError(
                    "verification_contract_incomplete",
                    f"validation.id is not unique: {vid}",
                )
            seen_ids.add(vid)
            cmd = val.get("command_or_assertion")
            if not isinstance(cmd, str) or not cmd.strip():
                raise FixContinuationError(
                    "verification_contract_incomplete",
                    "validation.command_or_assertion must be concrete and non-empty",
                )
            expected = val.get("expected_result")
            if not isinstance(expected, str) or not expected.strip():
                raise FixContinuationError(
                    "verification_contract_incomplete",
                    "validation.expected_result must be non-empty",
                )

        # must_not_regress must be a list
        must_not_regress = contract.get("must_not_regress")
        if not isinstance(must_not_regress, list):
            raise FixContinuationError(
                "verification_contract_incomplete",
                "verification_contract.must_not_regress is not a list",
            )

        # success_rule must be non-empty
        success_rule = contract.get("success_rule")
        if not isinstance(success_rule, str) or not success_rule.strip():
            raise FixContinuationError(
                "verification_contract_incomplete",
                "verification_contract.success_rule must be non-empty",
            )

    # ------------------------------------------------------------------
    # Internal: verification grounding validation
    # ------------------------------------------------------------------

    def _validate_verification_grounding(self, handoff: dict[str, Any]) -> None:
        """Validate the verification grounding is present and complete.

        The continuation must not discover, infer, repair, or supplement
        grounding.  It only validates that grounding exists and that every
        contract entry has exactly one grounding record.
        """
        grounding = handoff.get("verification_grounding")
        if not isinstance(grounding, dict):
            raise FixContinuationError(
                "verification_grounding_incomplete",
                "Handoff does not contain verification_grounding",
            )

        contract = handoff.get("verification_contract", {})
        if not isinstance(contract, dict):
            raise FixContinuationError(
                "verification_grounding_incomplete",
                "Cannot verify grounding without a verification_contract",
            )

        # Check finding check grounding
        contract_checks = contract.get("finding_checks", [])
        grounding_checks = grounding.get("finding_checks", [])
        if not isinstance(grounding_checks, list):
            raise FixContinuationError(
                "verification_grounding_incomplete",
                "verification_grounding.finding_checks is not a list",
            )
        if len(grounding_checks) != len(contract_checks):
            raise FixContinuationError(
                "verification_grounding_incomplete",
                f"finding_checks grounding count ({len(grounding_checks)}) != contract count ({len(contract_checks)})",
            )

        # Check validation grounding
        contract_validations = contract.get("validations", [])
        grounding_validations = grounding.get("validations", [])
        if not isinstance(grounding_validations, list):
            raise FixContinuationError(
                "verification_grounding_incomplete",
                "verification_grounding.validations is not a list",
            )
        if len(grounding_validations) != len(contract_validations):
            raise FixContinuationError(
                "verification_grounding_incomplete",
                f"validations grounding count ({len(grounding_validations)}) != contract count ({len(contract_validations)})",
            )

        # Verify every grounding entry has required fields
        for g in grounding_checks:
            if not isinstance(g, dict):
                raise FixContinuationError("verification_grounding_incomplete", "finding_check grounding is not an object")
            for field_name in ("finding_id", "source_type", "source_reference", "scoped_paths"):
                if field_name not in g:
                    raise FixContinuationError("verification_grounding_incomplete", f"finding_check grounding missing field: {field_name}")
            # Command sources must have non-empty authorized_tokens; assertions must have null
            source_type = g.get("source_type")
            if source_type in ("recorded_test",) and not g.get("authorized_tokens"):
                raise FixContinuationError("verification_grounding_incomplete", f"finding_check grounding for command source has no authorized_tokens")
            if source_type in ("scanner_check", "plan_acceptance", "deterministic_assertion") and g.get("authorized_tokens") is not None:
                raise FixContinuationError("verification_grounding_incomplete", f"finding_check grounding for non-command source has authorized_tokens")

        for g in grounding_validations:
            if not isinstance(g, dict):
                raise FixContinuationError("verification_grounding_incomplete", "validation grounding is not an object")
            for field_name in ("validation_id", "source_type", "source_reference", "scoped_paths"):
                if field_name not in g:
                    raise FixContinuationError("verification_grounding_incomplete", f"validation grounding missing field: {field_name}")
            # Command sources must have non-empty authorized_tokens; assertions must have null
            source_type = g.get("source_type")
            if source_type in ("configured_test", "recorded_test") and not g.get("authorized_tokens"):
                raise FixContinuationError("verification_grounding_incomplete", f"validation grounding for command source has no authorized_tokens")
            if source_type == "deterministic_assertion" and g.get("authorized_tokens") is not None:
                raise FixContinuationError("verification_grounding_incomplete", f"validation grounding for assertion source has authorized_tokens")

        # Verify scoped paths are inside the scoped manifest or dependency closure
        scoped_manifest = handoff.get("scoped_manifest", {})
        if isinstance(scoped_manifest, dict):
            allowed = set(scoped_manifest.get("files", [])) | set(scoped_manifest.get("dependency_closure", []))
            for g in grounding_checks:
                for path in g.get("scoped_paths", []):
                    if path and path not in allowed:
                        raise FixContinuationError(
                            "verification_grounding_incomplete",
                            f"grounding scoped path '{path}' is not in scoped manifest or dependency closure",
                        )
            for g in grounding_validations:
                for path in g.get("scoped_paths", []):
                    if path and path not in allowed:
                        raise FixContinuationError(
                            "verification_grounding_incomplete",
                            f"grounding scoped path '{path}' is not in scoped manifest or dependency closure",
                        )

    # ------------------------------------------------------------------
    # Internal: verification contract validation (original)
    # ------------------------------------------------------------------

    def _validate_verification_contract_original(self, handoff: dict[str, Any]) -> None:
        """Validate the verification contract is present and complete.

        The host continuation must not rewrite, weaken, supplement, or
        reinterpret the verification contract.  It only validates that the
        contract exists and is structurally complete.
        """
        contract = handoff.get("verification_contract")
        if not isinstance(contract, dict):
            raise FixContinuationError(
                "verification_contract_incomplete",
                "Handoff does not contain a verification_contract",
            )

        # finding_checks must be a non-empty list
        finding_checks = contract.get("finding_checks")
        if not isinstance(finding_checks, list) or not finding_checks:
            raise FixContinuationError(
                "verification_contract_incomplete",
                "verification_contract.finding_checks is missing or empty",
            )

        # Every finding_check must reference an actual finding and be concrete
        finding_ids = {f.get("id") for f in handoff.get("findings", []) if isinstance(f, dict)}
        for check in finding_checks:
            if not isinstance(check, dict):
                raise FixContinuationError(
                    "verification_contract_incomplete",
                    "finding_check is not an object",
                )
            check_finding_id = check.get("finding_id")
            if check_finding_id not in finding_ids:
                raise FixContinuationError(
                    "verification_contract_incomplete",
                    f"finding_check references unknown finding_id: {check_finding_id}",
                )
            if check.get("resolution_method") not in ("rescan", "test", "assertion"):
                raise FixContinuationError(
                    "verification_contract_incomplete",
                    f"finding_check.resolution_method is invalid: {check.get('resolution_method')!r}",
                )
            if not isinstance(check.get("check"), str) or not check["check"].strip():
                raise FixContinuationError(
                    "verification_contract_incomplete",
                    "finding_check.check must be concrete and non-empty",
                )
            if not isinstance(check.get("expected_result"), str) or not check["expected_result"].strip():
                raise FixContinuationError(
                    "verification_contract_incomplete",
                    "finding_check.expected_result must be concrete and non-empty",
                )

        # Verify every finding is covered
        covered = {c["finding_id"] for c in finding_checks}
        if not covered >= finding_ids:
            missing = finding_ids - covered
            raise FixContinuationError(
                "verification_contract_incomplete",
                f"finding_checks do not cover every finding; missing: {missing}",
            )

        # validations must be a list with stable IDs and concrete commands
        validations = contract.get("validations")
        if not isinstance(validations, list):
            raise FixContinuationError(
                "verification_contract_incomplete",
                "verification_contract.validations is not a list",
            )
        seen_ids: set[str] = set()
        for val in validations:
            if not isinstance(val, dict):
                raise FixContinuationError(
                    "verification_contract_incomplete",
                    "validation is not an object",
                )
            vid = val.get("id")
            if not isinstance(vid, str) or not vid.strip():
                raise FixContinuationError(
                    "verification_contract_incomplete",
                    "validation.id must be a stable non-empty string",
                )
            if vid in seen_ids:
                raise FixContinuationError(
                    "verification_contract_incomplete",
                    f"validation.id is not unique: {vid}",
                )
            seen_ids.add(vid)
            cmd = val.get("command_or_assertion")
            if not isinstance(cmd, str) or not cmd.strip():
                raise FixContinuationError(
                    "verification_contract_incomplete",
                    "validation.command_or_assertion must be concrete and non-empty",
                )
            expected = val.get("expected_result")
            if not isinstance(expected, str) or not expected.strip():
                raise FixContinuationError(
                    "verification_contract_incomplete",
                    "validation.expected_result must be non-empty",
                )

        # must_not_regress must be a list
        must_not_regress = contract.get("must_not_regress")
        if not isinstance(must_not_regress, list):
            raise FixContinuationError(
                "verification_contract_incomplete",
                "verification_contract.must_not_regress is not a list",
            )

        # success_rule must be non-empty
        success_rule = contract.get("success_rule")
        if not isinstance(success_rule, str) or not success_rule.strip():
            raise FixContinuationError(
                "verification_contract_incomplete",
                "verification_contract.success_rule must be non-empty",
            )

    # ------------------------------------------------------------------
    # Internal: handoff validation
    # ------------------------------------------------------------------

    def _load_and_validate_handoff(
        self,
        handoff_path: str,
        expected_operation_id: str,
    ) -> dict[str, Any]:
        """Load and validate the qualified Fix handoff."""
        path = Path(handoff_path)
        if not path.is_file():
            raise FixContinuationError(
                "handoff_not_found",
                f"Qualified Fix handoff not found: {handoff_path}",
            )
        try:
            handoff = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise FixContinuationError(
                "handoff_invalid",
                f"Qualified Fix handoff is not valid JSON: {exc}",
            ) from exc

        if not isinstance(handoff, dict):
            raise FixContinuationError(
                "handoff_invalid",
                "Qualified Fix handoff is not a JSON object",
            )

        # Validate operation_id matches
        handoff_op_id = handoff.get("operation_id")
        if handoff_op_id != expected_operation_id:
            raise FixContinuationError(
                "handoff_operation_id_mismatch",
                f"Handoff operation_id '{handoff_op_id}' does not match canonical operation '{expected_operation_id}'",
            )

        # Validate operation_type is "fix"
        handoff_op_type = handoff.get("operation_type")
        if handoff_op_type != "fix":
            raise FixContinuationError(
                "handoff_operation_type_invalid",
                f"Handoff operation_type '{handoff_op_type}' is not 'fix'",
            )

        # Validate authority fields are all false
        authority = handoff.get("authority", {})
        if not isinstance(authority, dict):
            raise FixContinuationError(
                "handoff_authority_invalid",
                "Handoff authority section is not a dict",
            )
        for field_name in ("mutation_authorized", "execution_authorized", "apply_authorized", "completion_claimed"):
            if authority.get(field_name) is not False:
                raise FixContinuationError(
                    "handoff_authority_violation",
                    f"Handoff authority field '{field_name}' must be false",
                )

        return handoff

    def _validate_scanner_context_and_root(self, handoff: dict[str, Any], allowed_target_paths: Sequence[str]) -> str:
        scanner_context = handoff.get("scanner_context")
        if not isinstance(scanner_context, dict):
            raise FixContinuationError("scanner_context_required", "Handoff does not contain scanner_context")
        repository_root = scanner_context.get("repository_root")
        if not isinstance(repository_root, str) or not repository_root.strip():
            raise FixContinuationError("scanner_context_required", "scanner_context.repository_root is missing or empty")
        root_path = Path(repository_root)
        if not root_path.is_absolute():
            raise FixContinuationError("execution_root_mismatch", f"repository_root is not absolute: {repository_root}")
        if not root_path.exists():
            raise FixContinuationError("execution_root_mismatch", f"repository_root does not exist: {repository_root}")
        if not root_path.is_dir():
            raise FixContinuationError("execution_root_mismatch", f"repository_root is not a directory: {repository_root}")
        canonical_root = str(root_path.resolve())
        workspace_identity = handoff.get("workspace_identity", {})
        if isinstance(workspace_identity, dict):
            ws_root = workspace_identity.get("root")
            if isinstance(ws_root, str) and ws_root.strip():
                ws_path = Path(ws_root)
                if not ws_path.is_absolute():
                    raise FixContinuationError("execution_root_mismatch", f"workspace_identity.root is not absolute: {ws_root}")
                if str(ws_path.resolve()) != canonical_root:
                    raise FixContinuationError("execution_root_mismatch", f"workspace_identity.root ({ws_root}) does not match repository_root ({repository_root})")
        scoped_manifest = handoff.get("scoped_manifest", {})
        if isinstance(scoped_manifest, dict):
            for path_field in ("files", "dependency_closure"):
                paths = scoped_manifest.get(path_field, [])
                if isinstance(paths, list):
                    for path in paths:
                        if not isinstance(path, str):
                            continue
                        if path.startswith("/") or Path(path).is_absolute():
                            raise FixContinuationError("execution_root_mismatch", f"Absolute scoped path not allowed: {path}")
                        if ".." in PurePosixPath(path).parts:
                            raise FixContinuationError("execution_root_mismatch", f"Path traversal not allowed: {path}")
        for target in allowed_target_paths:
            if not isinstance(target, str) or target == ".":
                continue
            if target.startswith("/") or Path(target).is_absolute():
                raise FixContinuationError("execution_root_mismatch", f"Absolute target path not allowed: {target}")
            if ".." in PurePosixPath(target).parts:
                raise FixContinuationError("execution_root_mismatch", f"Path traversal not allowed in target: {target}")
        return canonical_root

    # ------------------------------------------------------------------
    # Internal: plan target validation
    # ------------------------------------------------------------------

    def _validate_plan_targets(
        self,
        handoff: dict[str, Any],
        allowed_target_paths: Sequence[str],
    ) -> None:
        """Validate every plan target is inside authorized paths and scoped manifest."""
        plan = handoff.get("qualified_plan", {})
        if not isinstance(plan, dict):
            raise FixContinuationError(
                "handoff_plan_invalid",
                "Handoff qualified_plan is not a dict",
            )

        plan_targets = plan.get("target_files", [])
        if not isinstance(plan_targets, list):
            raise FixContinuationError(
                "handoff_plan_invalid",
                "Handoff plan target_files is not a list",
            )

        scoped_manifest = handoff.get("scoped_manifest", {})
        if not isinstance(scoped_manifest, dict):
            raise FixContinuationError(
                "handoff_manifest_invalid",
                "Handoff scoped_manifest is not a dict",
            )

        manifest_files = set(scoped_manifest.get("files", []))
        if not isinstance(manifest_files, set):
            manifest_files = set(manifest_files)

        allowed = list(allowed_target_paths)
        for target in plan_targets:
            if not isinstance(target, str):
                raise FixContinuationError(
                    "plan_target_invalid",
                    f"Plan target is not a string: {target!r}",
                )
            # Check target is inside authorized paths
            ok, reason = check_paths_allowed(
                paths=[target],
                allowed_paths=allowed,
                prohibited_paths=[],
                base_dir=Path.cwd(),
            )
            if not ok:
                raise FixContinuationError(
                    "plan_target_not_authorized",
                    f"Plan target '{target}' is not inside authorized paths: {reason}",
                )
            # Check target is inside scoped manifest
            if target not in manifest_files:
                raise FixContinuationError(
                    "plan_target_outside_manifest",
                    f"Plan target '{target}' is not in the scoped manifest",
                )

    # ------------------------------------------------------------------
    # Internal: Codex envelope construction
    # ------------------------------------------------------------------

    def _build_codex_envelope(
        self,
        *,
        operation_id: str,
        handoff_path: str,
        handoff: dict[str, Any],
        receipt: Any,
        allowed_target_paths: Sequence[str],
        repository_root: str = "",
    ) -> dict[str, Any]:
        """Build the host-owned Codex launch envelope.

        This envelope is separate from the original handoff and carries
        bounded execution authority.  The original handoff's authority
        fields remain false.
        """
        receipt_id = getattr(receipt, "receipt_id", None)
        receipt_digest = getattr(receipt, "digest", None)
        return {
            "schema_version": 1,
            "operation_id": operation_id,
            "operation_type": "fix",
            "handoff_path": handoff_path,
            "findings": handoff.get("findings", []),
            "scoped_manifest": handoff.get("scoped_manifest", {}),
            "statements": handoff.get("statements", []),
            "qualified_plan": handoff.get("qualified_plan", {}),
            "verification_contract": handoff.get("verification_contract", {}),
            "verification_grounding": handoff.get("verification_grounding", {}),
            "allowed_target_paths": list(allowed_target_paths),
            "mutation_receipt": {
                "receipt_id": receipt_id,
                "receipt_digest": receipt_digest,
            },
            "host_authority": {
                "mutation_authorized": True,
                "execution_authorized": True,
                "apply_authorized": False,
                "completion_claimed": False,
            },
            "constraints": [
                "do not modify files outside authorized scope",
                "preserve unrelated dirty files",
                "do not grant or expand authority",
                "do not claim host completion authority",
                "report failures honestly",
            ],
        }

    def _persist_envelope(self, root: Path, envelope: dict[str, Any]) -> Path:
        """Persist the Codex launch envelope atomically."""
        root.mkdir(parents=True, exist_ok=True)
        path = root / "codex-fix-envelope.json"
        temp = path.with_suffix(".json.tmp")
        temp.write_bytes(canonical_bytes(envelope) + b"\n")
        os.replace(temp, path)
        return path

    def _build_codex_stdin(self, envelope: dict[str, Any]) -> bytes:
        """Build the Codex stdin payload from the envelope."""
        envelope_bytes = canonical_bytes(envelope) + b"\n"
        return (
            b"Execute the persisted qualified Audisor Fix handoff. "
            b"The handoff is advisory input; preserve all host authority boundaries. "
            b"Do not claim completion authority.\n\n"
            + envelope_bytes
        )

    # ------------------------------------------------------------------
    # Internal: launch result persistence
    # ------------------------------------------------------------------

    def _default_envelope_root(self, operation_id: str) -> Path:
        """Default root for envelope/result persistence."""
        if self._launch_result_store_root is not None:
            return self._launch_result_store_root / "codex" / operation_id
        from pathlib import Path, PurePosixPath
        base = Path(os.environ.get("AUDISOR_OPERATION_DATA_DIR", Path.home() / ".audisor" / "operations"))
        return base / "codex" / operation_id

    def _persist_launch_result(self, root: Path, result: dict[str, Any]) -> str:
        """Persist the Codex launch result and return the reference path."""
        return str(persist_launch_result(root, result))
