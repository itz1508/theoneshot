"""A-Flow MCP server — host-neutral foundation demonstrated with the Codex hook.

Exposes exactly two tools:

* ``aflow_review`` — accepts a complete schema-v1 analysis request and explicit
  contract-assembly inputs, calls the real ``aflow.analyze()``, and on a clean
  decision persists the operation record and writes a hook-compatible
  active-state envelope.
* ``aflow_status`` — reads the active-state envelope and independently
  recomputes lock, contract, readiness, and drift validity.

Start with::

    python -m audisor.aflow_mcp_server

This module imports only public names from the runtime lifecycle and A-Flow.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import mcp.types as types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from audisor.audisor_lifecycle.active_state import (
    default_state_root,
    read_active_state,
)
from audisor.audisor_lifecycle.adapter import verify_contract
from audisor.audisor_lifecycle.contract import AudisorLifecycleError, verify_lock
from audisor.audisor_lifecycle.hook import verify_active_state
from audisor.audisor_lifecycle.review_contract import review_and_lock
from audisor.operations.store import AudisorOperationStore

_INSTRUCTIONS = (
    "A-Flow plan review and execution-state tools. "
    "aflow_review requires a complete structured analysis request. "
    "aflow_status reports the current active execution state."
)


def _schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    """Build a strict JSON Schema that rejects unknown properties."""
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


_TOOLS: list[types.Tool] = [
    types.Tool(
        name="aflow_review",
        description=(
            "Review a complete A-Flow analysis request. On a clean decision, "
            "creates the execution contract, primary lock, and active state. "
            "Requires complete structured input; raw plan text is not accepted."
        ),
        inputSchema=_schema(
            {
                "analysis_request": {
                    "type": "object",
                    "description": "Complete schema-v1 analysis request (8 fields).",
                },
                "accepted_task_input": {
                    "type": "object",
                    "description": "Task input for contract assembly.",
                },
                "candidate_implementation_plan": {
                    "type": "object",
                    "description": "Plan with all 7 PLAN_SECTIONS.",
                },
                "authority": {
                    "type": "object",
                    "description": "Authority mapping (allowed_paths, prohibited_paths, etc.).",
                },
                "baseline_evidence": {
                    "description": "Baseline evidence for contract assembly.",
                },
                "accepted_constraints": {
                    "description": "Constraints for contract assembly.",
                },
                "required_outputs": {
                    "description": "Required outputs for contract assembly.",
                },
                "operation_id": {
                    "type": "string",
                    "description": "Caller-supplied operation identifier.",
                },
                "state_root": {
                    "type": ["string", "null"],
                    "description": "Optional state directory path.",
                },
            },
            [
                "analysis_request",
                "accepted_task_input",
                "candidate_implementation_plan",
                "authority",
                "baseline_evidence",
                "accepted_constraints",
                "required_outputs",
                "operation_id",
            ],
        ),
    ),
    types.Tool(
        name="aflow_status",
        description=(
            "Report the current active execution state. Independently "
            "recomputes lock, contract, readiness, and drift validity. "
            "Optionally reports operation store state for a given operation_id."
        ),
        inputSchema=_schema(
            {
                "state_root": {
                    "type": ["string", "null"],
                    "description": "Optional state directory path.",
                },
                "operation_id": {
                    "type": ["string", "null"],
                    "description": "Optional operation ID to query from the operation store.",
                },
            },
            [],
        ),
    ),
]


def _resolve_state_root(arguments: dict[str, Any] | None) -> Path:
    """Resolve state root from environment (authoritative) or arguments.

    Raises:
        ValueError: If both env and tool argument specify different roots.
    """
    env = os.environ.get("AUDISOR_STATE_ROOT") or os.environ.get("AFLOW_STATE_ROOT")
    explicit = arguments.get("state_root") if arguments else None
    if env and explicit and Path(explicit) != Path(env):
        raise ValueError(
            f"state_root conflict: env={env!r} vs argument={explicit!r}; "
            "environment is authoritative"
        )
    if env:
        return Path(env)
    if explicit:
        return Path(explicit)
    return default_state_root()


def _dispatch_review(arguments: dict[str, Any]) -> dict[str, Any]:
    """Handle aflow_review tool call."""
    try:
        state_root = _resolve_state_root(arguments)
    except ValueError as exc:
        return {
            "status": "blocked",
            "decision": "error",
            "blocking": True,
            "execution_ready": False,
            "findings": [],
            "lock_state": {"present": False, "valid": False},
            "contract_sha256": None,
            "state_path": None,
            "operation_id": arguments.get("operation_id", ""),
            "error": {"code": "state_root_conflict", "detail": str(exc)},
        }
    try:
        result = review_and_lock(
            analysis_request=arguments["analysis_request"],
            accepted_task_input=arguments["accepted_task_input"],
            candidate_implementation_plan=arguments["candidate_implementation_plan"],
            authority=arguments["authority"],
            baseline_evidence=arguments["baseline_evidence"],
            accepted_constraints=arguments["accepted_constraints"],
            required_outputs=arguments["required_outputs"],
            operation_id=arguments["operation_id"],
            state_root=state_root,
        )
        return result
    except AudisorLifecycleError as exc:
        return {
            "status": "blocked",
            "decision": "error",
            "blocking": True,
            "execution_ready": False,
            "findings": [],
            "lock_state": {"present": False, "valid": False},
            "contract_sha256": None,
            "state_path": None,
            "operation_id": arguments.get("operation_id", ""),
            "error": {"code": "lifecycle_error", "detail": str(exc)},
        }
    except Exception as exc:
        return {
            "status": "blocked",
            "decision": "error",
            "blocking": True,
            "execution_ready": False,
            "findings": [],
            "lock_state": {"present": False, "valid": False},
            "contract_sha256": None,
            "state_path": None,
            "operation_id": arguments.get("operation_id", ""),
            "error": {"code": "internal_error", "detail": f"{type(exc).__name__}: {exc}"},
        }


def _dispatch_status(arguments: dict[str, Any] | None) -> dict[str, Any]:
    """Handle aflow_status tool call."""
    try:
        state_root = _resolve_state_root(arguments)
    except ValueError as exc:
        return {
            "status": "error",
            "lock_present": False,
            "lock_valid": False,
            "contract_valid": False,
            "readiness": None,
            "drift_valid": False,
            "error": {"code": "state_root_conflict", "detail": str(exc)},
        }
    try:
        state = read_active_state(state_root)
    except AudisorLifecycleError as exc:
        return {
            "status": "error",
            "lock_present": False,
            "lock_valid": False,
            "contract_valid": False,
            "readiness": None,
            "drift_valid": False,
            "error": str(exc),
        }

    if state is None:
        result: dict[str, Any] = {
            "status": "ok",
            "lock_present": False,
            "lock_valid": False,
            "contract_valid": False,
            "readiness": None,
            "drift_valid": False,
        }
    else:
        # Independently recompute validity
        lock = state.get("primary_lock")
        contract = state.get("execution_contract")
        lock_valid = isinstance(lock, dict) and verify_lock(lock)
        contract_valid = isinstance(contract, dict) and verify_contract(contract)
        drift_valid = state.get("drift_state") == "valid"

        # Full envelope verification
        envelope_valid, reason, verified_contract = verify_active_state(state)

        readiness = None
        if isinstance(contract, dict):
            readiness_obj = contract.get("readiness")
            if isinstance(readiness_obj, dict):
                readiness = readiness_obj.get("aflow_decision")

        result = {
            "status": "ok",
            "lock_present": True,
            "lock_valid": lock_valid,
            "contract_valid": contract_valid,
            "readiness": readiness,
            "drift_valid": drift_valid,
            "envelope_valid": envelope_valid,
            "operation_id": state.get("operation_id"),
        }

    # Query operation store if operation_id is provided
    if arguments:
        op_id = arguments.get("operation_id")
        if op_id:
            try:
                op_store = AudisorOperationStore(state_root / "operations")
                op_state = op_store.get(op_id)
                if op_state is not None:
                    result["operation_state"] = op_state.to_mapping()
                else:
                    result["operation_state"] = None
            except (OSError, json.JSONDecodeError, KeyError) as exc:
                result["operation_state"] = None
                result["operation_store_error"] = {
                    "code": "store_unreadable",
                    "detail": f"{type(exc).__name__}: {exc}",
                }

    return result


def _dispatch(name: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
    """Route a tool call to the appropriate handler."""
    if name == "aflow_review":
        if not arguments:
            return {"status": "blocked", "error": {"code": "missing_arguments", "detail": "arguments required"}}
        return _dispatch_review(arguments)
    if name == "aflow_status":
        return _dispatch_status(arguments)
    return {"status": "blocked", "error": {"code": "unknown_tool", "detail": f"unknown tool: {name}"}}


def create_server() -> Server:
    """Create and configure the A-Flow MCP server."""
    server: Server = Server("audisor-aflow", version="0.9.0", instructions=_INSTRUCTIONS)

    @server.list_tools()
    async def handle_list_tools() -> list[types.Tool]:
        return _TOOLS

    @server.call_tool()
    async def handle_call_tool(name: str, arguments: dict[str, Any] | None) -> list[types.TextContent]:
        result = _dispatch(name, arguments)
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
