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
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

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
    """Result of a Fix-to-Codex continuation launch."""

    operation_id: str
    handoff_path: str
    codex_envelope_path: str
    codex_result_reference: str
    pid: int | None
    exit_code: int
    outcome: str
    resolved_command: tuple[str, ...]
    working_directory: str

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
        launcher: Callable[..., tuple[int | None, int, str, tuple[str, ...]]] = launch_codex,
        launch_result_store_root: Path | None = None,
    ) -> None:
        self._launcher = launcher
        self._launch_result_store_root = launch_result_store_root

    def run(
        self,
        *,
        operation_id: str,
        handoff_path: str,
        receipt: Any,
        allowed_target_paths: Sequence[str],
        working_directory: Path,
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

        # 2. Validate plan targets are inside authorized paths
        self._validate_plan_targets(handoff, allowed_target_paths)

        # 3. Build the host-owned Codex launch envelope
        envelope_root = codex_envelope_root or self._default_envelope_root(operation_id)
        envelope = self._build_codex_envelope(
            operation_id=operation_id,
            handoff_path=handoff_path,
            handoff=handoff,
            receipt=receipt,
            allowed_target_paths=allowed_target_paths,
        )
        envelope_path = self._persist_envelope(envelope_root, envelope)

        # 4. Build Codex stdin from the envelope
        stdin_bytes = self._build_codex_stdin(envelope)

        # 5. Launch Codex once
        try:
            pid, exit_code, outcome, argv = self._launcher(
                stdin_bytes=stdin_bytes,
                cwd=working_directory,
            )
        except CodexLaunchError as exc:
            # Persist the failure and re-raise as FixContinuationError
            failure_result = {
                "operation_id": operation_id,
                "outcome": "codex_failed",
                "failure_code": exc.code,
                "handoff_path": handoff_path,
                "codex_envelope_path": str(envelope_path),
            }
            result_ref = self._persist_launch_result(envelope_root, failure_result)
            raise FixContinuationError(
                "codex_launch_failed",
                f"Codex launch failed: {exc}",
            ) from exc

        # 6. Persist the launch result under the same operation ID
        launch_result = {
            "operation_id": operation_id,
            "outcome": outcome,
            "pid": pid,
            "exit_code": exit_code,
            "handoff_path": handoff_path,
            "codex_envelope_path": str(envelope_path),
        }
        result_ref = self._persist_launch_result(envelope_root, launch_result)

        return FixContinuationResult(
            operation_id=operation_id,
            handoff_path=handoff_path,
            codex_envelope_path=str(envelope_path),
            codex_result_reference=result_ref,
            pid=pid,
            exit_code=exit_code,
            outcome=outcome,
            resolved_command=argv,
            working_directory=str(working_directory),
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
        from pathlib import Path
        base = Path(os.environ.get("AUDISOR_OPERATION_DATA_DIR", Path.home() / ".audisor" / "operations"))
        return base / "codex" / operation_id

    def _persist_launch_result(self, root: Path, result: dict[str, Any]) -> str:
        """Persist the Codex launch result and return the reference path."""
        return str(persist_launch_result(root, result))