"""Real MCP stdio transport tests for the A-Flow MCP server.

These tests launch the actual server subprocess and communicate via the
MCP stdio transport, proving registration, dispatch, strict-schema
rejection, and result encoding — not merely in-process function calls.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


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


def _submit_arguments(**overrides: object) -> dict:
    base = {
        "artifact_id": "artifact.transport-test",
        "artifact_type": "plan",
        "status": "collecting",
        "content": "draft in progress",
        "intent": "prove the transport",
        "context": "isolated test state root",
    }
    base.update(overrides)
    return base


def _assert_rejected(result: object, *, detail_fragment: str) -> None:
    """Accept SDK-level isError or the server's own input_validation error."""
    if getattr(result, "isError", False):
        return
    payload = _result_json(result)
    assert payload["status"] == "error", payload
    assert payload["stage"] == "input_validation", payload
    assert detail_fragment in payload["detail"], payload


class TestMcpTransport:
    """Real MCP stdio transport integration tests."""

    def test_tools_list_returns_lifecycle_and_management_tools(self) -> None:
        asyncio.run(self._tools_list())

    async def _tools_list(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            params = _server_params(tmp)
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools = (await session.list_tools()).tools
                    names = {t.name for t in tools}
                    assert names == {
                        "aflow_submit_artifact",
                        "aflow_last_result",
                        "aflow_provider_status",
                        "aflow_list_issues",
                        "aflow_get_issue",
                    }
                    for tool in tools:
                        assert tool.inputSchema.get("additionalProperties") is False, (
                            f"{tool.name} inputSchema must emit additionalProperties=false"
                        )
                    submit = next(t for t in tools if t.name == "aflow_submit_artifact")
                    for name in ("artifact_id", "artifact_type", "status"):
                        assert submit.inputSchema["properties"][name].get("minLength") == 1, (
                            f"{name} must advertise minLength=1 to match the trigger schema"
                        )

    def test_empty_identity_fields_rejected_through_transport(self) -> None:
        """Empty artifact_id/artifact_type/status are rejected at MCP validation,
        matching the minLength: 1 contract in aflow-artifact-trigger.schema.json."""
        asyncio.run(self._empty_identity_fields_rejected())

    async def _empty_identity_fields_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            params = _server_params(tmp)
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    for field in ("artifact_id", "artifact_type", "status"):
                        result = await session.call_tool(
                            "aflow_submit_artifact", _submit_arguments(**{field: ""})
                        )
                        _assert_rejected(result, detail_fragment=field)

    def test_unknown_property_rejected_through_transport(self) -> None:
        """Prove unknown-property rejection through a live stdio call."""
        asyncio.run(self._unknown_property_rejected())

    async def _unknown_property_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            params = _server_params(tmp)
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()

                    submit = await session.call_tool(
                        "aflow_submit_artifact",
                        _submit_arguments(unknown_extra="should_fail"),
                    )
                    _assert_rejected(submit, detail_fragment="unknown_extra")

                    last = await session.call_tool(
                        "aflow_last_result",
                        {"artifact_id": "artifact.x", "bogus_field": True},
                    )
                    _assert_rejected(last, detail_fragment="bogus_field")

                    for tool_name, arguments in (
                        ("aflow_provider_status", {"bogus_field": True}),
                        ("aflow_list_issues", {"bogus_field": True}),
                        ("aflow_get_issue", {"issue_id": "aflow-00000000000000000000", "bogus_field": True}),
                    ):
                        result = await session.call_tool(tool_name, arguments)
                        _assert_rejected(result, detail_fragment="bogus_field")

    def test_missing_property_rejected_through_transport(self) -> None:
        asyncio.run(self._missing_property_rejected())

    async def _missing_property_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            params = _server_params(tmp)
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    arguments = _submit_arguments()
                    del arguments["context"]
                    result = await session.call_tool("aflow_submit_artifact", arguments)
                    _assert_rejected(result, detail_fragment="context")

    def test_non_draft_complete_skips_through_transport(self) -> None:
        """Any status other than draft_complete returns skip, no worker involved."""
        asyncio.run(self._skip_path())

    async def _skip_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            params = _server_params(tmp)
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = _result_json(
                        await session.call_tool(
                            "aflow_submit_artifact", _submit_arguments(status="collecting")
                        )
                    )
                    assert result["status"] == "skip"
                    assert "draft_complete" in result["reason"]

    def test_last_result_reattaches_to_persisted_result(self) -> None:
        """After a submit completes, aflow_last_result returns the identical JSON."""
        asyncio.run(self._reattach())

    async def _reattach(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            params = _server_params(tmp)
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    submitted = _result_json(
                        await session.call_tool(
                            "aflow_submit_artifact",
                            _submit_arguments(artifact_id="artifact.reattach"),
                        )
                    )
                    reattached = _result_json(
                        await session.call_tool(
                            "aflow_last_result", {"artifact_id": "artifact.reattach"}
                        )
                    )
                    assert reattached == submitted

    def test_last_result_unknown_artifact_is_error(self) -> None:
        asyncio.run(self._last_result_unknown())

    async def _last_result_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            params = _server_params(tmp)
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = _result_json(
                        await session.call_tool(
                            "aflow_last_result", {"artifact_id": "artifact.never"}
                        )
                    )
                    assert result["status"] == "error"
                    assert result["stage"] == "last_result"
