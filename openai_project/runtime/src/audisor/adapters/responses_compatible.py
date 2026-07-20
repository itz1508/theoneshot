"""Responses-compatible adapter scope definition.

The "responses-compatible" adapter bridges Audisor to OpenAI's
Responses API (and compatible implementations).  It is explicitly
NOT a general-purpose execution adapter.

Scope:
- READ-ONLY operations: analyze, validate, review
- NO mutation operations: build, fix are NOT supported
- Streaming: supported for real-time analysis feedback
- Tools: NOT supported (Responses API tool use is out of scope)
- Artifacts: NOT supported (inline text only)

This adapter is for:
- Chat-based plan review
- Gap analysis discussion
- Validation result explanation
- Read-only repository inspection

This adapter is NOT for:
- File writes
- Command execution
- Build orchestration
- Fix application

The adapter translates AudisorOperationResult into Responses API
message format and back.  It never executes tools or mutations.
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


class ResponsesCompatibleCapabilities(HostCapabilities):
    """Capabilities for the responses-compatible adapter."""

    def __init__(self) -> None:
        super().__init__(
            supports_streaming=True,
            supports_tools=False,
            supports_artifacts=False,
            max_request_size_bytes=500_000,
            max_response_size_bytes=500_000,
            supported_content_types=("text/plain", "application/json"),
        )


class ResponsesRequestAdapter(HostRequestAdapter):
    """Translate Responses API requests to AudisorOperationRequest."""

    def translate_request(
        self,
        host_request: Mapping[str, Any],
        *,
        host_identity: str = "responses_api",
    ) -> AudisorOperationRequest:
        """Translate a Responses API request to canonical Audisor request.

        Only 'analyze' and 'validate' modes are permitted.
        """
        mode = host_request.get("mode", "analyze")
        if mode not in ("analyze", "validate"):
            raise ValueError(
                f"Responses-compatible adapter only supports analyze/validate, "
                f"not {mode}"
            )

        # Extract request content
        request_content = host_request.get("content", {})
        if isinstance(request_content, str):
            request_content = {"prompt": request_content}

        # Build authority from host context
        from audisor.schemas.authority import AuthorityContext, AuthoritySource, PermissionSet

        authority = AuthorityContext(
            source=AuthoritySource(
                source_type="host_adapter",
                grant_id=host_identity,
                host_identity=host_identity,
            ),
            permissions=PermissionSet(
                allowed_paths=["."],
                prohibited_paths=[".git", ".codex", "audisor-state"],
                allowed_tools=["read_file"],
                prohibited_tools=["write_file", "replace_in_file", "execute_command", "delete_file"],
            ),
            scope="responses_compatible",
        )

        return AudisorOperationRequest(
            operation_id=host_request.get("operation_id", "responses-unknown"),
            mode=mode,
            request=request_content,
            authority=authority,
            constraints=host_request.get("constraints", {}),
            host_capabilities=ResponsesCompatibleCapabilities(),
            host_context={"adapter": "responses_compatible"},
        )

    def detect_capabilities(self, host_request: Mapping[str, Any]) -> HostCapabilities:
        return ResponsesCompatibleCapabilities()


class ResponsesResponseAdapter(HostResponseAdapter):
    """Translate AudisorOperationResult to Responses API format."""

    def translate_result(
        self,
        result: AudisorOperationResult,
        *,
        host_capabilities: HostCapabilities | None = None,
    ) -> Mapping[str, Any]:
        """Translate canonical result to Responses API message format."""
        return {
            "role": "assistant",
            "content": result.summary or "Analysis complete.",
            "metadata": {
                "operation_id": result.operation_id,
                "status": result.status,
                "findings_count": len(result.findings),
                "evidence_count": len(result.evidence),
            },
        }

    def translate_error(
        self,
        error: Mapping[str, Any],
        *,
        host_capabilities: HostCapabilities | None = None,
    ) -> Mapping[str, Any]:
        """Translate canonical error to Responses API error format."""
        return {
            "role": "assistant",
            "content": f"Error: {error.get('message', 'Unknown error')}",
            "metadata": {
                "error": True,
                "category": error.get("category", "unknown"),
                "code": error.get("code", "unknown"),
            },
        }