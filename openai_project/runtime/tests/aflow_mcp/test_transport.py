"""Real MCP stdio transport tests for the A-Flow MCP server.

These tests launch the actual server subprocess and communicate via the
MCP stdio transport, proving registration, dispatch, schema rejection,
and result encoding — not merely in-process function calls.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# Ensure the runtime src is importable for fixture construction
RUNTIME_SRC = Path(__file__).resolve().parents[2] / "src"
AFLOW_SRC = Path(__file__).resolve().parents[3] / "aflow" / "src"
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "aflow_contract"


def _result_json(result: object) -> dict:
    content = getattr(result, "content")
    text = getattr(content[0], "text")
    return json.loads(text)


def _server_params(state_root: str) -> StdioServerParameters:
    """Build server parameters with isolated state root."""
    env = os.environ.copy()
    env["AUDISOR_STATE_ROOT"] = state_root
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "audisor.aflow_mcp_server"],
        env=env,
    )


def _ready_input() -> dict:
    return json.loads((FIXTURES / "ready-input.json").read_text(encoding="utf-8"))


def _clean_analysis_request() -> dict:
    """Build a complete analysis request that passes aflow.analyze() cleanly."""
    sys.path.insert(0, str(AFLOW_SRC))
    try:
        from aflow.fixtures.factory import request_bundle
        return request_bundle()
    finally:
        sys.path.pop(0)


def _review_arguments(state_root: str) -> dict:
    """Build complete aflow_review arguments."""
    data = _ready_input()
    return {
        "analysis_request": _clean_analysis_request(),
        "accepted_task_input": data["accepted_task_input"],
        "candidate_implementation_plan": data["candidate_implementation_plan"],
        "authority": data["authority"],
        "baseline_evidence": data["baseline_evidence"],
        "accepted_constraints": data["accepted_constraints"],
        "required_outputs": data["required_outputs"],
        "operation_id": "op.mcp-transport-test",
        "state_root": state_root,
    }


class TestMcpTransport:
    """Real MCP stdio transport integration tests."""

    def test_tools_list_returns_exactly_two_tools(self) -> None:
        asyncio.run(self._tools_list())

    async def _tools_list(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            params = _server_params(tmp)
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools = (await session.list_tools()).tools
                    names = {t.name for t in tools}
                    assert names == {"aflow_review", "aflow_status"}

    def test_unknown_property_rejected_through_transport(self) -> None:
        """Prove additionalProperties:false is enforced at transport level."""
        asyncio.run(self._unknown_property_rejected())

    async def _unknown_property_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            params = _server_params(tmp)
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()

                    # Verify schema advertises additionalProperties=false
                    tools = {t.name: t for t in (await session.list_tools()).tools}
                    for name, tool in tools.items():
                        assert tool.inputSchema.get("additionalProperties") is False, (
                            f"{name} inputSchema must emit additionalProperties=false"
                        )

                    # Prove rejection through real transport call
                    result = await session.call_tool(
                        "aflow_status",
                        {"state_root": tmp, "unknown_extra": "should_fail"},
                    )
                    assert getattr(result, "isError", False), (
                        "Server must return isError=True for unknown property; "
                        "unknown-property rejection is not enforced."
                    )

    def test_review_and_status_through_transport(self) -> None:
        """Full flow: review creates state, status reports it."""
        asyncio.run(self._review_and_status())

    async def _review_and_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            params = _server_params(tmp)
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()

                    # Status before review: no lock
                    status_before = _result_json(
                        await session.call_tool("aflow_status", {"state_root": tmp})
                    )
                    assert status_before["status"] == "ok"
                    assert status_before["lock_present"] is False

                    # Review with complete valid input
                    args = _review_arguments(tmp)
                    review_result = _result_json(
                        await session.call_tool("aflow_review", args)
                    )
                    assert review_result["status"] == "ok"
                    assert review_result["decision"] == "no_material_gap"
                    assert review_result["blocking"] is False
                    assert review_result["execution_ready"] is True
                    assert review_result["lock_state"]["present"] is True
                    assert review_result["lock_state"]["valid"] is True
                    assert review_result["contract_sha256"] is not None

                    # Status after review: lock present and valid
                    status_after = _result_json(
                        await session.call_tool("aflow_status", {"state_root": tmp})
                    )
                    assert status_after["status"] == "ok"
                    assert status_after["lock_present"] is True
                    assert status_after["lock_valid"] is True
                    assert status_after["contract_valid"] is True
                    assert status_after["readiness"] == "no_material_gap"
                    assert status_after["drift_valid"] is True
                    assert status_after["envelope_valid"] is True

    def test_blocking_review_does_not_create_state(self) -> None:
        """A schema-invalid request blocks without creating state."""
        asyncio.run(self._blocking_review())

    async def _blocking_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            params = _server_params(tmp)
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()

                    data = _ready_input()
                    args = {
                        "analysis_request": {"schema_version": "1.0.0", "analysis_id": "bad"},
                        "accepted_task_input": data["accepted_task_input"],
                        "candidate_implementation_plan": data["candidate_implementation_plan"],
                        "authority": data["authority"],
                        "baseline_evidence": data["baseline_evidence"],
                        "accepted_constraints": data["accepted_constraints"],
                        "required_outputs": data["required_outputs"],
                        "operation_id": "op.blocked",
                        "state_root": tmp,
                    }
                    result = _result_json(
                        await session.call_tool("aflow_review", args)
                    )
                    assert result["status"] == "blocked"
                    assert result["blocking"] is True
                    assert result["lock_state"]["present"] is False

                    # Confirm no state was written
                    status = _result_json(
                        await session.call_tool("aflow_status", {"state_root": tmp})
                    )
                    assert status["lock_present"] is False

    def test_operation_store_and_status_through_transport(self) -> None:
        """Prove operation persistence, aflow_status(operation_id), not-found, and rejection."""
        asyncio.run(self._operation_store_and_status())

    async def _operation_store_and_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            params = _server_params(tmp)
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()

                    # 1. Review with a fixed operation_id and valid clean request
                    op_id = "op.transport-store-proof"
                    args = _review_arguments(tmp)
                    args["operation_id"] = op_id
                    review_result = _result_json(
                        await session.call_tool("aflow_review", args)
                    )
                    assert review_result["status"] == "ok"
                    assert review_result["operation_status"] == "completed"

                    # 2. aflow_status with that operation_id
                    status = _result_json(
                        await session.call_tool("aflow_status", {
                            "state_root": tmp,
                            "operation_id": op_id,
                        })
                    )

                    # 3. Assert active-state validity recomputed
                    assert status["lock_valid"] is True
                    assert status["contract_valid"] is True
                    assert status["drift_valid"] is True
                    assert status["envelope_valid"] is True

                    # 4. Assert operation state from store
                    op_state = status["operation_state"]
                    assert op_state is not None
                    assert op_state["operation_id"] == op_id
                    assert op_state["status"] == "completed"
                    # Artifacts contain decision, execution-contract, and lock
                    artifact_types = {a["artifact_type"] for a in op_state["artifacts"]}
                    assert "analysis" in artifact_types
                    assert "contract" in artifact_types
                    assert "lock" in artifact_types

                    # 5. aflow_status with unknown operation_id → not-found
                    status_unknown = _result_json(
                        await session.call_tool("aflow_status", {
                            "state_root": tmp,
                            "operation_id": "op.does-not-exist",
                        })
                    )
                    assert status_unknown["operation_state"] is None

                    # 6. aflow_status with unknown property → transport rejection
                    rejected = await session.call_tool(
                        "aflow_status",
                        {"state_root": tmp, "bogus_field": True},
                    )
                    assert getattr(rejected, "isError", False), (
                        "Unknown property on aflow_status must be rejected at transport"
                    )
