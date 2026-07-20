"""Mutation enforcer for Audisor operations.

Owns final path/limit check, write authorization, and mutation receipt.
Distinct from the executor and the worker.  All mutations must pass through
here before any filesystem or command execution.

Replaces duplicate hook path logic with canonical path_security.py usage.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Mapping

from audisor.schemas.authority import CanonicalAuthority
from audisor.schemas.errors import AudisorRuntimeError
from audisor.security.path_security import (
    PathSecurityError,
    canonicalize_path,
    check_paths_allowed,
    validate_relative_path,
)


@dataclass(frozen=True)
class MutationReceipt:
    """Receipt for an authorized mutation."""

    receipt_id: str
    operation_id: str
    authorized_paths: tuple[str, ...]
    prohibited_paths: tuple[str, ...]
    authorized_tools: tuple[str, ...]
    prohibited_tools: tuple[str, ...]
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))
    digest: str = ""

    def __post_init__(self) -> None:
        if not self.digest:
            # Compute digest from immutable fields
            payload = {
                "receipt_id": self.receipt_id,
                "operation_id": self.operation_id,
                "authorized_paths": list(self.authorized_paths),
                "prohibited_paths": list(self.prohibited_paths),
                "authorized_tools": list(self.authorized_tools),
                "prohibited_tools": list(self.prohibited_tools),
                "timestamp": self.timestamp,
            }
            digest = hashlib.sha256(
                json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            object.__setattr__(self, "digest", digest)

    def to_mapping(self) -> dict[str, Any]:
        return {
            "receipt_id": self.receipt_id,
            "operation_id": self.operation_id,
            "authorized_paths": list(self.authorized_paths),
            "prohibited_paths": list(self.prohibited_paths),
            "authorized_tools": list(self.authorized_tools),
            "prohibited_tools": list(self.prohibited_tools),
            "timestamp": self.timestamp,
            "digest": self.digest,
        }


class MutationEnforcer:
    """Enforces authority boundaries on mutation operations.

    All filesystem writes, command executions, and tool invocations that
    could mutate state must be authorized by this enforcer before execution.
    """

    def __init__(self, base_dir: Path | None = None) -> None:
        self._base_dir = base_dir or Path.cwd()

    def _generate_receipt_id(self, operation_id: str) -> str:
        """Generate a unique receipt ID for an operation."""
        timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        payload = f"{operation_id}:{timestamp}"
        return f"mut-{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:16]}"

    def authorize_mutation(
        self,
        operation_id: str,
        authority: CanonicalAuthority,
        *,
        target_paths: list[str] | None = None,
        tool_name: str | None = None,
    ) -> MutationReceipt:
        """Authorize a mutation operation, returning a receipt.

        Args:
            operation_id: The operation requesting mutation.
            authority: The canonical authority context.
            target_paths: Paths the mutation targets (for path checks).
            tool_name: Tool being invoked (for tool checks).

        Returns:
            A MutationReceipt if authorization succeeds.

        Raises:
            AudisorRuntimeError: If authorization fails.
        """
        # Validate target paths against authority
        if target_paths is not None and target_paths:
            allowed, reason = check_paths_allowed(
                paths=target_paths,
                allowed_paths=authority.allowed_paths,
                prohibited_paths=authority.prohibited_paths,
                base_dir=self._base_dir,
            )
            if not allowed:
                raise AudisorRuntimeError(
                    category="authority",
                    stage="authority_check",
                    code="mutation_paths_not_authorized",
                    message=f"Mutation blocked: {reason}",
                    detail=f"operation_id={operation_id}, targets={target_paths}",
                )

        # Validate tool against authority
        if tool_name is not None:
            tool_lower = tool_name.casefold()
            if authority.prohibited_tools and tool_lower in {t.casefold() for t in authority.prohibited_tools}:
                raise AudisorRuntimeError(
                    category="authority",
                    stage="authority_check",
                    code="mutation_tool_prohibited",
                    message=f"Tool '{tool_name}' is prohibited by authority",
                    detail=f"operation_id={operation_id}, tool={tool_name}",
                )
            if authority.allowed_tools and tool_lower not in {t.casefold() for t in authority.allowed_tools}:
                raise AudisorRuntimeError(
                    category="authority",
                    stage="authority_check",
                    code="mutation_tool_not_allowed",
                    message=f"Tool '{tool_name}' is not in allowed tools list",
                    detail=f"operation_id={operation_id}, tool={tool_name}",
                )

        # All checks passed; issue receipt
        receipt = MutationReceipt(
            receipt_id=self._generate_receipt_id(operation_id),
            operation_id=operation_id,
            authorized_paths=authority.allowed_paths,
            prohibited_paths=authority.prohibited_paths,
            authorized_tools=authority.allowed_tools,
            prohibited_tools=authority.prohibited_tools,
        )
        return receipt

    def validate_receipt(self, receipt: MutationReceipt, operation_id: str) -> bool:
        """Validate that a receipt matches its claimed operation and is unaltered.

        Args:
            receipt: The receipt to validate.
            operation_id: The expected operation ID.

        Returns:
            True if the receipt is valid for the operation.

        Raises:
            AudisorRuntimeError: If the receipt is invalid or tampered.
        """
        if receipt.operation_id != operation_id:
            raise AudisorRuntimeError(
                category="authority",
                stage="authority_check",
                code="receipt_operation_mismatch",
                message="Mutation receipt does not match operation",
            )

        # Recompute digest
        payload = {
            "receipt_id": receipt.receipt_id,
            "operation_id": receipt.operation_id,
            "authorized_paths": list(receipt.authorized_paths),
            "prohibited_paths": list(receipt.prohibited_paths),
            "authorized_tools": list(receipt.authorized_tools),
            "prohibited_tools": list(receipt.prohibited_tools),
            "timestamp": receipt.timestamp,
        }
        expected_digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

        if receipt.digest != expected_digest:
            raise AudisorRuntimeError(
                category="authority",
                stage="authority_check",
                code="receipt_digest_mismatch",
                message="Mutation receipt has been tampered with",
            )

        return True

    def check_read_only(
        self,
        operation_id: str,
        authority: CanonicalAuthority,
        paths: list[str],
    ) -> MutationReceipt:
        """Authorize a read-only operation (no mutation, just path validation).

        All paths must be within allowed scope, but no tool checks are performed.
        """
        if not paths:
            # No paths to check; issue a read-only receipt
            return MutationReceipt(
                receipt_id=self._generate_receipt_id(operation_id),
                operation_id=operation_id,
                authorized_paths=authority.allowed_paths,
                prohibited_paths=authority.prohibited_paths,
                authorized_tools=authority.allowed_tools,
                prohibited_tools=authority.prohibited_tools,
            )

        allowed, reason = check_paths_allowed(
            paths=paths,
            allowed_paths=authority.allowed_paths,
            prohibited_paths=authority.prohibited_paths,
            base_dir=self._base_dir,
        )
        if not allowed:
            raise AudisorRuntimeError(
                category="authority",
                stage="authority_check",
                code="read_paths_not_authorized",
                message=f"Read blocked: {reason}",
                detail=f"operation_id={operation_id}, paths={paths}",
            )

        return MutationReceipt(
            receipt_id=self._generate_receipt_id(operation_id),
            operation_id=operation_id,
            authorized_paths=authority.allowed_paths,
            prohibited_paths=authority.prohibited_paths,
            authorized_tools=authority.allowed_tools,
            prohibited_tools=authority.prohibited_tools,
        )
