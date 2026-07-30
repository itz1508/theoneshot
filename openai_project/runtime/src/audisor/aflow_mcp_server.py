"""A-Flow MCP server — canonical artifact lifecycle surface.

Exposes the canonical lifecycle plus redacted management tools:

* ``aflow_submit_artifact`` — accepts the six-field artifact trigger and runs
  one deterministic lifecycle cycle (gap_finding -> gap_fixing ->
  unresolved-gap barrier -> evaluation -> success_criteria -> fixture_design),
  returning the full result JSON.
* ``aflow_last_result`` — returns the most recent persisted result for an
  artifact, so a client can reattach after a transport timeout.

Start with::

    python -m audisor.aflow_mcp_server

This module imports only public names from the runtime lifecycle.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import mcp.types as types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from audisor.audisor_lifecycle.artifact_flow import (
    PersistedResultError,
    read_last_result,
    run_artifact_lifecycle,
)
from audisor.audisor_lifecycle.management import (
    get_issue,
    initialize_management_state,
    list_issues,
    provider_status,
)

_INSTRUCTIONS = (
    "Canonical A-Flow artifact lifecycle tools. Submit a completed artifact "
    "draft with aflow_submit_artifact; the runtime runs gap finding, gap "
    "fixing, the unresolved-gap barrier, evaluation, success criteria, and "
    "fixture design in fixed order and returns the result. Use "
    "aflow_last_result to reattach to the most recent persisted result. "
    "Management tools expose workspace-scoped redacted provider and issue state."
)


def _schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    """Build a strict JSON Schema that rejects unknown properties."""
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


_SUBMIT_REQUIRED = ["artifact_id", "artifact_type", "status", "content", "intent", "context"]

# Mirrors the minLength: 1 constraints in aflow-artifact-trigger.schema.json.
_SUBMIT_NON_EMPTY = ["artifact_id", "artifact_type", "status"]

_TOOLS: list[types.Tool] = [
    types.Tool(
        name="aflow_submit_artifact",
        description=(
            "Run one A-Flow artifact review cycle. Only status "
            "'draft_complete' with non-empty content runs the lifecycle; "
            "anything else returns a skip. Result status is exactly one of "
            "improved | unresolved_gap | skip | error. On unresolved_gap, "
            "report the listed requirements to the user; on improved, the "
            "handoff package carries the improved artifact, success "
            "criteria, and fixture cases."
        ),
        inputSchema=_schema(
            {
                "artifact_id": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Stable identifier for this artifact.",
                },
                "artifact_type": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Kind of artifact, e.g. plan, design, spec.",
                },
                "status": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Artifact state; only 'draft_complete' triggers a cycle.",
                },
                "content": {
                    "type": "string",
                    "description": "Full artifact text to review.",
                },
                "intent": {
                    "type": "string",
                    "description": "What the artifact is meant to achieve.",
                },
                "context": {
                    "type": "string",
                    "description": "Repository facts, constraints, and decisions relevant to review.",
                },
            },
            _SUBMIT_REQUIRED,
        ),
    ),
    types.Tool(
        name="aflow_last_result",
        description=(
            "Return the most recent persisted lifecycle result for an "
            "artifact_id, for reattaching after a client timeout."
        ),
        inputSchema=_schema(
            {
                "artifact_id": {
                    "type": "string",
                    "description": "Artifact identifier used at submission.",
                },
            },
            ["artifact_id"],
        ),
    ),
    types.Tool(
        name="aflow_provider_status",
        description=(
            "Return redacted A-Flow provider configuration, readiness, "
            "fallback, and active-run state. probe=true performs an explicit "
            "bounded structured-output provider call."
        ),
        inputSchema=_schema(
            {"probe": {"type": "boolean", "description": "Run an explicit bounded provider probe."}},
            [],
        ),
    ),
    types.Tool(
        name="aflow_list_issues",
        description="List redacted workspace-scoped A-Flow issue metadata.",
        inputSchema=_schema(
            {
                "cursor": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
            [],
        ),
    ),
    types.Tool(
        name="aflow_get_issue",
        description="Return one redacted workspace-scoped Root Cause Issue.",
        inputSchema=_schema(
            {"issue_id": {"type": "string", "minLength": 1}},
            ["issue_id"],
        ),
    ),
]


def _reject_unknown(arguments: dict[str, Any], allowed: set[str]) -> dict[str, Any] | None:
    """Runtime unknown-property rejection, independent of client-side schema use."""
    unknown = sorted(set(arguments) - allowed)
    if unknown:
        return {
            "status": "error",
            "stage": "input_validation",
            "detail": f"unknown properties: {', '.join(unknown)}",
        }
    return None


def _dispatch_submit(arguments: dict[str, Any]) -> dict[str, Any]:
    """Handle aflow_submit_artifact tool call."""
    rejection = _reject_unknown(arguments, set(_SUBMIT_REQUIRED))
    if rejection:
        return rejection
    missing = sorted(name for name in _SUBMIT_REQUIRED if name not in arguments)
    if missing:
        return {
            "status": "error",
            "stage": "input_validation",
            "detail": f"missing properties: {', '.join(missing)}",
        }
    non_string = sorted(
        name for name in _SUBMIT_REQUIRED if not isinstance(arguments[name], str)
    )
    if non_string:
        return {
            "status": "error",
            "stage": "input_validation",
            "detail": f"properties must be strings: {', '.join(non_string)}",
        }
    empty = sorted(name for name in _SUBMIT_NON_EMPTY if not arguments[name])
    if empty:
        return {
            "status": "error",
            "stage": "input_validation",
            "detail": f"properties must be non-empty: {', '.join(empty)}",
        }
    try:
        return run_artifact_lifecycle(arguments)
    except Exception as exc:  # defensive: the engine already bounds worker errors
        return {
            "status": "error",
            "stage": "lifecycle",
            "detail": f"{type(exc).__name__}: {exc}",
        }


def _dispatch_last_result(arguments: dict[str, Any]) -> dict[str, Any]:
    """Handle aflow_last_result tool call."""
    rejection = _reject_unknown(arguments, {"artifact_id"})
    if rejection:
        return rejection
    artifact_id = arguments.get("artifact_id")
    if not isinstance(artifact_id, str) or not artifact_id.strip():
        return {
            "status": "error",
            "stage": "input_validation",
            "detail": "artifact_id must be a non-empty string",
        }
    try:
        result = read_last_result(artifact_id)
    except PersistedResultError as exc:
        # Corrupt or incomplete persisted state is a bounded structured
        # error, never silently treated as a completed outcome.
        return {
            "status": "error",
            "stage": "persisted_state",
            "detail": str(exc),
            "artifact_id": artifact_id,
            "state_path": exc.state_path,
            "failure": exc.failure,
        }
    if result is None:
        return {
            "status": "error",
            "stage": "last_result",
            "detail": f"no persisted result for artifact_id {artifact_id!r}",
        }
    return dict(result)


def _dispatch_provider_status(arguments: dict[str, Any]) -> dict[str, Any]:
    rejection = _reject_unknown(arguments, {"probe"})
    if rejection:
        return rejection
    probe = arguments.get("probe", False)
    if not isinstance(probe, bool):
        return {"status": "error", "stage": "input_validation", "detail": "probe must be a boolean"}
    try:
        return provider_status(probe=probe)
    except Exception as exc:
        return {"status": "error", "stage": "management", "detail": f"{type(exc).__name__}: {exc}"}


def _dispatch_list_issues(arguments: dict[str, Any]) -> dict[str, Any]:
    rejection = _reject_unknown(arguments, {"cursor", "limit"})
    if rejection:
        return rejection
    cursor = arguments.get("cursor")
    limit = arguments.get("limit", 50)
    if cursor is not None and not isinstance(cursor, str):
        return {"status": "error", "stage": "input_validation", "detail": "cursor must be a string"}
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
        return {"status": "error", "stage": "input_validation", "detail": "limit must be an integer from 1 to 100"}
    return list_issues(cursor=cursor, limit=limit)


def _dispatch_get_issue(arguments: dict[str, Any]) -> dict[str, Any]:
    rejection = _reject_unknown(arguments, {"issue_id"})
    if rejection:
        return rejection
    issue_id = arguments.get("issue_id")
    if not isinstance(issue_id, str) or not issue_id.strip():
        return {"status": "error", "stage": "input_validation", "detail": "issue_id must be a non-empty string"}
    issue = get_issue(issue_id)
    if issue is None:
        return {"status": "error", "stage": "issue_lookup", "detail": f"no issue found for issue_id {issue_id!r}"}
    return issue


def _dispatch(name: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
    """Route a tool call to the appropriate handler."""
    if name == "aflow_submit_artifact":
        if not arguments:
            return {
                "status": "error",
                "stage": "input_validation",
                "detail": "arguments required",
            }
        return _dispatch_submit(arguments)
    if name == "aflow_last_result":
        if not arguments:
            return {
                "status": "error",
                "stage": "input_validation",
                "detail": "arguments required",
            }
        return _dispatch_last_result(arguments)
    if name == "aflow_provider_status":
        return _dispatch_provider_status(arguments or {})
    if name == "aflow_list_issues":
        return _dispatch_list_issues(arguments or {})
    if name == "aflow_get_issue":
        if not arguments:
            return {"status": "error", "stage": "input_validation", "detail": "arguments required"}
        return _dispatch_get_issue(arguments)
    return {
        "status": "error",
        "stage": "dispatch",
        "detail": f"unknown tool: {name}",
    }


def create_server() -> Server:
    """Create and configure the A-Flow MCP server."""
    initialize_management_state()
    server: Server = Server("audisor-aflow", version="0.10.0", instructions=_INSTRUCTIONS)

    @server.list_tools()
    async def handle_list_tools() -> list[types.Tool]:
        return _TOOLS

    @server.call_tool()
    async def handle_call_tool(name: str, arguments: dict[str, Any] | None) -> list[types.TextContent]:
        result = await asyncio.to_thread(_dispatch, name, arguments)
        return [types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]

    return server


async def _run() -> None:
    server = create_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main() -> None:
    """Entry point for ``python -m audisor.aflow_mcp_server``."""
    asyncio.run(_run())


if __name__ == "__main__":
    main()
