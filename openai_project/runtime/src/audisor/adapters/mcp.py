"""MCP (Model Context Protocol) host adapter for Audisor.

Translates MCP-specific requests into canonical AudisorOperationRequest
and AudisorOperationResult back into MCP-compatible responses.

This adapter is a thin translation layer ONLY.  It does NOT:
- Execute operations
- Alter authority
- Weaken safety limits
- Inject MCP-specific fields into the canonical request
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


class MCPCapabilities(HostCapabilities):
    """Capabilities for the MCP adapter."""

    def __init__(self) -> None:
        super().__init__(
            supports_streaming=False,
            supports_tools=True,
            supports_artifacts=False,
            max_request_size_bytes=1_000_000,
            max_response_size_bytes=1_000_000,
            supported_content_types=("text/plain", "application/json"),
        )


class MCPRequestAdapter(HostRequestAdapter):
    """Translate MCP requests to AudisorOperationRequest."""

    def translate_request(
        self,
        host_request: Mapping[str, Any],
        *,
        host_identity: str = "mcp",
    ) -> AudisorOperationRequest:
        """Translate an MCP request to canonical Audisor request.

        MCP requests typically contain:
        - operation_id: str
        - mode: "build" | "fix" | "analyze" | "validate"
        - request: dict with task details
        - authority: optional authority override
        """
        mode = host_request.get("mode", "analyze")
        if mode not in ("build", "fix", "analyze", "validate"):
            raise ValueError(f"Unsupported MCP mode: {mode}")

        # Build authority from host context or defaults
        authority_data = host_request.get("authority", {})
        if isinstance(authority_data, dict):
            source = AuthoritySource(
                source_type="mcp_server",
                grant_id=authority_data.get("grant_id", host_identity),
                host_identity=host_identity,
            )
            permissions = PermissionSet(
                allowed_paths=authority_data.get("allowed_paths", ["."]),
                prohibited_paths=authority_data.get("prohibited_paths", [".git", ".codex"]),
                allowed_tools=authority_data.get("allowed_tools", []),
                prohibited_tools=authority_data.get("prohibited_tools", []),
            )
            authority = AuthorityContext(
                source=source,
                permissions=permissions,
                scope=authority_data.get("scope", "repository"),
            )
        else:
            # Default authority for MCP
            authority = AuthorityContext(
                source=AuthoritySource(
                    source_type="mcp_server",
                    grant_id=host_identity,
                    host_identity=host_identity,
                ),
                permissions=PermissionSet(
                    allowed_paths=["."],
                    prohibited_paths=[".git", ".codex"],
                    allowed_tools=[],
                    prohibited_tools=[],
                ),
                scope="repository",
            )

        return AudisorOperationRequest(
            operation_id=host_request.get("operation_id", "mcp-unknown"),
            mode=mode,
            request=host_request.get("request", {}),
            authority=authority,
            constraints=host_request.get("constraints", {}),
            host_capabilities=MCPCapabilities(),
            host_context={"adapter": "mcp", "host_identity": host_identity},
        )

    def detect_capabilities(self, host_request: Mapping[str, Any]) -> HostCapabilities:
        return MCPCapabilities()


class MCPResponseAdapter(HostResponseAdapter):
    """Translate AudisorOperationResult to MCP response format."""

    def translate_result(
        self,
        result: AudisorOperationResult,
        *,
        host_capabilities: HostCapabilities | None = None,
    ) -> Mapping[str, Any]:
        """Translate canonical result to MCP response format."""
        response: dict[str, Any] = {
            "operation_id": result.operation_id,
            "status": result.status,
            "summary": result.summary,
            "findings": [dict(f) for f in result.findings],
            "evidence": [dict(e) for e in result.evidence],
        }

        if result.plan is not None:
            response["plan"] = dict(result.plan)
        if result.execution is not None:
            response["execution"] = dict(result.execution)
        if result.validation is not None:
            response["validation"] = dict(result.validation)
        if result.error is not None:
            response["error"] = dict(result.error) if isinstance(result.error, dict) else str(result.error)

        # MCP-specific fields
        response["mcp_compatible"] = True
        response["tool_results_available"] = result.status == "completed"

        return response

    def translate_error(
        self,
        error: Mapping[str, Any],
        *,
        host_capabilities: HostCapabilities | None = None,
    ) -> Mapping[str, Any]:
        """Translate canonical error to MCP error format."""
        return {
            "operation_id": error.get("operation_id", "unknown"),
            "status": "error",
            "error": {
                "category": error.get("category", "unknown"),
                "code": error.get("code", "unknown"),
                "message": error.get("message", "Unknown error"),
                "detail": error.get("detail", ""),
            },
            "mcp_compatible": True,
            "tool_results_available": False,
        }