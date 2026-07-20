"""Codex host adapter for Audisor.

Translates Codex-specific requests into canonical AudisorOperationRequest
and AudisorOperationResult back into Codex-compatible responses.

This adapter is a thin translation layer ONLY.  It does NOT:
- Execute operations
- Alter authority
- Weaken safety limits
- Inject Codex-specific fields into the canonical request
"""

from __future__ import annotations

from typing import Any, Mapping

from audisor.adapters.protocol import (
    AudisorOperationRequest,
    AudisorOperationResult,
    HostCapabilities,
    HostRequestAdapter,
    HostResponseAdapter,
)
from audisor.schemas.authority import AuthorityContext, AuthoritySource, PermissionSet


class CodexCapabilities(HostCapabilities):
    """Capabilities for the Codex adapter."""

    def __init__(self) -> None:
        super().__init__(
            supports_streaming=False,
            supports_tools=True,
            supports_artifacts=True,
            max_request_size_bytes=1_000_000,
            max_response_size_bytes=1_000_000,
            supported_content_types=("text/plain", "application/json"),
        )


class CodexRequestAdapter(HostRequestAdapter):
    """Translate Codex requests to AudisorOperationRequest."""

    def translate_request(
        self,
        host_request: Mapping[str, Any],
        *,
        host_identity: str = "codex",
    ) -> AudisorOperationRequest:
        """Translate a Codex request to canonical Audisor request.

        Codex requests typically contain:
        - operation_id: str
        - mode: "build" | "fix" | "analyze" | "validate"
        - request: dict with task details
        - authority: optional authority override
        - constraints: optional constraints
        """
        mode = host_request.get("mode", "build")
        if mode not in ("build", "fix", "analyze", "validate"):
            raise ValueError(f"Unsupported Codex mode: {mode}")

        # Build authority from host context or defaults
        authority_data = host_request.get("authority", {})
        if isinstance(authority_data, dict):
            source = AuthoritySource(
                source_type="codex",
                grant_id=authority_data.get("grant_id", host_identity),
                host_identity=host_identity,
            )
            permissions = PermissionSet(
                allowed_paths=authority_data.get("allowed_paths", ["."]),
                prohibited_paths=authority_data.get("prohibited_paths", [".git", ".codex", "audisor-state"]),
                allowed_tools=authority_data.get("allowed_tools", [
                    "read_file", "write_file", "replace_in_file", "execute_command"
                ]),
                prohibited_tools=authority_data.get("prohibited_tools", ["delete_file", "move_file"]),
            )
            authority = AuthorityContext(
                source=source,
                permissions=permissions,
                scope=authority_data.get("scope", "repository"),
            )
        else:
            # Default authority for Codex
            authority = AuthorityContext(
                source=AuthoritySource(
                    source_type="codex",
                    grant_id=host_identity,
                    host_identity=host_identity,
                ),
                permissions=PermissionSet(
                    allowed_paths=["."],
                    prohibited_paths=[".git", ".codex", "audisor-state"],
                    allowed_tools=[
                        "read_file", "write_file", "replace_in_file", "execute_command"
                    ],
                    prohibited_tools=["delete_file", "move_file"],
                ),
                scope="repository",
            )

        return AudisorOperationRequest(
            operation_id=host_request.get("operation_id", "codex-unknown"),
            mode=mode,
            request=host_request.get("request", {}),
            authority=authority,
            constraints=host_request.get("constraints", {}),
            host_capabilities=CodexCapabilities(),
            host_context={"adapter": "codex", "host_identity": host_identity},
        )

    def detect_capabilities(self, host_request: Mapping[str, Any]) -> HostCapabilities:
        return CodexCapabilities()


class CodexResponseAdapter(HostResponseAdapter):
    """Translate AudisorOperationResult to Codex response format."""

    def translate_result(
        self,
        result: AudisorOperationResult,
        *,
        host_capabilities: HostCapabilities | None = None,
    ) -> Mapping[str, Any]:
        """Translate canonical result to Codex response format."""
        response: dict[str, Any] = {
            "operation_id": result.operation_id,
            "status": result.status,
            "summary": result.summary,
            "findings": [dict(f) for f in result.findings],
            "evidence": [dict(e) for e in result.evidence],
            "artifacts": [dict(a) for a in result.artifacts],
        }

        if result.plan is not None:
            response["plan"] = dict(result.plan)
        if result.execution is not None:
            response["execution"] = dict(result.execution)
        if result.validation is not None:
            response["validation"] = dict(result.validation)
        if result.error is not None:
            response["error"] = result.error.model_dump(mode="json")

        # Codex-specific fields
        response["codex_compatible"] = True
        response["continuation_permitted"] = result.status in ("completed", "accepted")

        return response

    def translate_error(
        self,
        error: Mapping[str, Any],
        *,
        host_capabilities: HostCapabilities | None = None,
    ) -> Mapping[str, Any]:
        """Translate canonical error to Codex error format."""
        return {
            "operation_id": error.get("operation_id", "unknown"),
            "status": "error",
            "error": {
                "category": error.get("category", "unknown"),
                "code": error.get("code", "unknown"),
                "message": error.get("message", "Unknown error"),
                "detail": error.get("detail", ""),
            },
            "continuation_permitted": False,
            "codex_compatible": True,
        }