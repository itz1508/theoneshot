"""CLI host adapter for Audisor.

Translates CLI-specific requests into canonical AudisorOperationRequest
and AudisorOperationResult back into CLI-compatible responses (plain text,
JSON, or exit codes).

This adapter is a thin translation layer ONLY.  It does NOT:
- Execute operations
- Alter authority
- Weaken safety limits
- Inject CLI-specific fields into the canonical request
"""

from __future__ import annotations

import json
from typing import Any, Mapping

from audisor.adapters.protocol import (
    AudisorOperationRequest,
    AudisorOperationResult,
    HostCapabilities,
    HostRequestAdapter,
    HostResponseAdapter,
)
from audisor.schemas.authority import AuthorityContext, AuthoritySource, PermissionSet


class CLICapabilities(HostCapabilities):
    """Capabilities for the CLI adapter."""

    def __init__(self) -> None:
        super().__init__(
            supports_streaming=False,
            supports_tools=False,
            supports_artifacts=False,
            max_request_size_bytes=1_000_000,
            max_response_size_bytes=1_000_000,
            supported_content_types=("text/plain", "application/json"),
        )


class CLIRequestAdapter(HostRequestAdapter):
    """Translate CLI requests to AudisorOperationRequest."""

    def translate_request(
        self,
        host_request: Mapping[str, Any],
        *,
        host_identity: str = "cli",
    ) -> AudisorOperationRequest:
        """Translate a CLI request to canonical Audisor request.

        CLI requests typically contain:
        - operation_id: str (auto-generated if not provided)
        - mode: "build" | "fix" | "analyze" | "validate"
        - request: dict with task details
        - authority: optional authority override
        """
        mode = host_request.get("mode", "build")
        if mode not in ("build", "fix", "analyze", "validate"):
            raise ValueError(f"Unsupported CLI mode: {mode}")

        # Build authority from host context or defaults
        authority_data = host_request.get("authority", {})
        if isinstance(authority_data, dict):
            source = AuthoritySource(
                source_type="user",
                grant_id=authority_data.get("grant_id", host_identity),
                host_identity=host_identity,
            )
            permissions = PermissionSet(
                allowed_paths=authority_data.get("allowed_paths", ["."]),
                prohibited_paths=authority_data.get("prohibited_paths", []),
                allowed_tools=authority_data.get("allowed_tools", []),
                prohibited_tools=authority_data.get("prohibited_tools", []),
            )
            authority = AuthorityContext(
                source=source,
                permissions=permissions,
                scope=authority_data.get("scope", "repository"),
            )
        else:
            # Default authority for CLI (permissive)
            authority = AuthorityContext(
                source=AuthoritySource(
                    source_type="user",
                    grant_id=host_identity,
                    host_identity=host_identity,
                ),
                permissions=PermissionSet(
                    allowed_paths=["."],
                    prohibited_paths=[],
                    allowed_tools=[],
                    prohibited_tools=[],
                ),
                scope="repository",
            )

        return AudisorOperationRequest(
            operation_id=host_request.get("operation_id", "cli-unknown"),
            mode=mode,
            request=host_request.get("request", {}),
            authority=authority,
            constraints=host_request.get("constraints", {}),
            host_capabilities=CLICapabilities(),
            host_context={"adapter": "cli", "host_identity": host_identity},
        )

    def detect_capabilities(self, host_request: Mapping[str, Any]) -> HostCapabilities:
        return CLICapabilities()


class CLIResponseAdapter(HostResponseAdapter):
    """Translate AudisorOperationResult to CLI response format."""

    def translate_result(
        self,
        result: AudisorOperationResult,
        *,
        host_capabilities: HostCapabilities | None = None,
    ) -> Mapping[str, Any]:
        """Translate canonical result to CLI response format.

        CLI responses are plain JSON with minimal nesting.
        """
        response: dict[str, Any] = {
            "operation_id": result.operation_id,
            "status": result.status,
            "summary": result.summary,
        }

        if result.findings:
            response["findings"] = [dict(f) for f in result.findings]
        if result.evidence:
            response["evidence"] = [dict(e) for e in result.evidence]
        if result.artifacts:
            response["artifacts"] = [dict(a) for a in result.artifacts]

        if result.plan is not None:
            response["plan"] = dict(result.plan)
        if result.execution is not None:
            response["execution"] = dict(result.execution)
        if result.validation is not None:
            response["validation"] = dict(result.validation)
        if result.error is not None:
            response["error"] = dict(result.error) if isinstance(result.error, dict) else str(result.error)

        # CLI-specific fields
        response["cli_compatible"] = True
        response["exit_code"] = 0 if result.status in ("completed", "accepted") else 1

        return response

    def translate_error(
        self,
        error: Mapping[str, Any],
        *,
        host_capabilities: HostCapabilities | None = None,
    ) -> Mapping[str, Any]:
        """Translate canonical error to CLI error format."""
        return {
            "operation_id": error.get("operation_id", "unknown"),
            "status": "error",
            "error": {
                "category": error.get("category", "unknown"),
                "code": error.get("code", "unknown"),
                "message": error.get("message", "Unknown error"),
                "detail": error.get("detail", ""),
            },
            "cli_compatible": True,
            "exit_code": 1,
        }

    @staticmethod
    def format_for_terminal(result: Mapping[str, Any]) -> str:
        """Format a CLI response for terminal output."""
        status = result.get("status", "unknown")
        summary = result.get("summary", "")

        lines = [
            f"Audisor CLI Result",
            f"Status: {status}",
            f"Operation: {result.get('operation_id', 'unknown')}",
        ]

        if summary:
            lines.append(f"Summary: {summary}")

        error = result.get("error")
        if error:
            lines.append(f"Error: {error.get('message', 'Unknown error')}")

        findings = result.get("findings", [])
        if findings:
            lines.append(f"Findings: {len(findings)}")

        return "\n".join(lines)