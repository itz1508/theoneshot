"""Tests for PlanningAdapter and ExecutionAdapter.

Proves: plan creation/resume, execution/resume, outcome interpretation,
plan parse fallback, and suspension envelope structure.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock

import pytest

from operation_controller.tool_loop import (
    LoopContinuation,
    ProviderResponse,
    ToolExecutionResult,
    ToolLoop,
    ToolLoopConfig,
)
from operation_controller.adapters.planning import LLMPlanningAdapter
from operation_controller.adapters.execution import LLMExecutionAdapter


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _make_tool_call(call_id: str, name: str, args: str = "{}") -> dict:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": args},
    }


class FakeProvider:
    """Scripted provider returning pre-configured responses."""

    def __init__(self, responses: list[ProviderResponse]) -> None:
        self._responses = list(responses)
        self._idx = 0

    def call(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        model: str | None,
        max_tokens: int,
        timeout_seconds: float,
    ) -> ProviderResponse:
        if self._idx >= len(self._responses):
            return ProviderResponse(text="exhausted")
        r = self._responses[self._idx]
        self._idx += 1
        return r


# ─── PlanningAdapter tests ────────────────────────────────────────────────────


class TestLLMPlanningAdapter:
    def test_create_plan_completes_with_json_plan(self) -> None:
        """Provider returns a plan in ```json``` block — adapter parses it."""
        plan_json = '{"summary": "Fix auth", "steps": [{"description": "patch login"}], "risks": []}'
        reply = f"Here is my plan:\n```json\n{plan_json}\n```\nLet me know."
        provider = FakeProvider([
            ProviderResponse(text=reply, finish_reason="stop",
                             usage={"prompt_tokens": 50, "completion_tokens": 30, "total_tokens": 80}),
        ])
        adapter = LLMPlanningAdapter(provider)
        result = adapter.create_plan("fix the auth bug", {})

        assert result["status"] == "completed"
        assert result["plan"]["summary"] == "Fix auth"
        assert result["plan"]["steps"][0]["description"] == "patch login"
        assert result["plan"]["risks"] == []

    def test_create_plan_suspends_for_frontend_tool(self) -> None:
        """Provider requests file_read — adapter returns suspension."""
        provider = FakeProvider([
            ProviderResponse(
                tool_calls=[_make_tool_call("c1", "file_read", '{"path": "src/main.py"}')],
                usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            ),
        ])
        adapter = LLMPlanningAdapter(provider)
        result = adapter.create_plan("fix the bug", {})

        assert result["status"] == "suspended"
        assert result["suspend_state"] == "suspended_for_tool_result"
        assert result["reason"] == "planning_tool_execution"
        assert len(result["pending_calls"]) == 1
        assert result["pending_calls"][0]["call_id"] == "c1"
        assert result["pending_calls"][0]["tool_name"] == "file_read"
        assert "continuation" in result
        assert result["suspension_id"].startswith("susp-")

    def test_resume_plan_with_tool_result(self) -> None:
        """Resume after suspension with tool result — adapter completes."""
        # First: create a suspension to get continuation data
        provider1 = FakeProvider([
            ProviderResponse(
                tool_calls=[_make_tool_call("c1", "file_read", '{"path": "x.py"}')],
            ),
        ])
        adapter = LLMPlanningAdapter(provider1)
        first_result = adapter.create_plan("analyze", {})
        assert first_result["status"] == "suspended"

        # Build a mock record with continuation artifact
        record = MagicMock()
        record.artifacts = {"continuation": first_result["continuation"]}

        # Resume with a new provider that completes
        plan_json = '{"summary": "Done", "steps": [], "risks": []}'
        provider2 = FakeProvider([
            ProviderResponse(text=f"```json\n{plan_json}\n```", finish_reason="stop"),
        ])
        adapter2 = LLMPlanningAdapter(provider2)
        resume_result = adapter2.resume_plan(
            record,
            {"tool_results": [{"call_id": "c1", "tool_name": "file_read", "output": "contents", "status": "success"}]},
        )

        assert resume_result["status"] == "completed"
        assert resume_result["plan"]["summary"] == "Done"

    def test_plan_parse_fallback(self) -> None:
        """Non-JSON response still returns completed with fallback plan."""
        provider = FakeProvider([
            ProviderResponse(text="I think we should refactor the module first.", finish_reason="stop"),
        ])
        adapter = LLMPlanningAdapter(provider)
        result = adapter.create_plan("refactor", {})

        assert result["status"] == "completed"
        # Fallback plan should have summary from text and risks warning
        assert "refactor" in result["plan"]["summary"].lower()
        assert len(result["plan"]["risks"]) > 0

    def test_create_plan_failed(self) -> None:
        """Provider error results in failed status."""

        class FailProvider:
            def call(self, **kwargs) -> ProviderResponse:
                raise RuntimeError("network error")

        adapter = LLMPlanningAdapter(FailProvider())
        result = adapter.create_plan("anything", {})
        assert result["status"] == "failed"
        assert "provider_error" in result["error"]


# ─── ExecutionAdapter tests ──────────────────────────────────────────────────


class TestLLMExecutionAdapter:
    def test_execute_completes(self) -> None:
        """No tool calls — execution completes directly."""
        provider = FakeProvider([
            ProviderResponse(text="All steps executed successfully.", finish_reason="stop",
                             usage={"prompt_tokens": 30, "completion_tokens": 20, "total_tokens": 50}),
        ])
        adapter = LLMExecutionAdapter(provider)
        result = adapter.execute("op-1", {"summary": "test plan"}, {})

        assert result["status"] == "completed"
        assert "successfully" in result["output"]

    def test_execute_suspends_for_approval(self) -> None:
        """shell_exec requires approval — execution suspends."""
        provider = FakeProvider([
            ProviderResponse(
                tool_calls=[_make_tool_call("c1", "shell_exec", '{"command": "npm test"}')],
            ),
        ])
        adapter = LLMExecutionAdapter(provider)
        result = adapter.execute("op-1", {"summary": "run tests"}, {})

        assert result["status"] == "suspended"
        assert result["suspend_state"] == "suspended_for_approval"
        assert "approval" in result["reason"].lower()
        assert result["tool_call"]["call_id"] == "c1"
        assert result["tool_call"]["tool_name"] == "shell_exec"
        assert "continuation" in result

    def test_resume_execution_after_approval(self) -> None:
        """Resume after approval suspension — execution completes."""
        # Create suspension first
        provider1 = FakeProvider([
            ProviderResponse(
                tool_calls=[_make_tool_call("c1", "shell_exec", '{"command": "npm test"}')],
            ),
        ])
        adapter1 = LLMExecutionAdapter(provider1)
        first_result = adapter1.execute("op-1", {"summary": "test"}, {})
        assert first_result["status"] == "suspended"

        # Resume with completion
        record = MagicMock()
        record.artifacts = {"continuation": first_result["continuation"]}

        provider2 = FakeProvider([
            ProviderResponse(text="Tests passed successfully.", finish_reason="stop"),
        ])
        adapter2 = LLMExecutionAdapter(provider2)
        resume_result = adapter2.resume_execution(
            record,
            {"tool_results": [{"call_id": "c1", "tool_name": "shell_exec", "output": "ok", "status": "success"}]},
        )

        assert resume_result["status"] == "completed"
        assert "successfully" in resume_result["output"].lower()

    def test_resume_execution_no_continuation_fails(self) -> None:
        """Resume with missing continuation data returns failure."""
        record = MagicMock()
        record.artifacts = {}

        adapter = LLMExecutionAdapter(FakeProvider([]))
        result = adapter.resume_execution(record, {})
        assert result["status"] == "failed"
        assert "continuation" in result["error"]

    def test_execute_suspends_for_frontend_tool(self) -> None:
        """file_read (no approval needed) suspends for frontend execution."""
        provider = FakeProvider([
            ProviderResponse(
                tool_calls=[_make_tool_call("c1", "file_read", '{"path": "src/app.py"}')],
            ),
        ])
        adapter = LLMExecutionAdapter(provider)
        result = adapter.execute("op-1", {"summary": "read file"}, {})

        # file_read is allowed but not approval-required, and is a frontend tool
        # file_read goes to frontend as SuspendedForTool
        assert result["status"] == "suspended"
        assert result["suspend_state"] == "suspended_for_tool_result"
        assert result["pending_calls"][0]["tool_name"] == "file_read"

    def test_execute_file_write_prohibited(self) -> None:
        """file_write is prohibited — model request is blocked by policy."""
        provider = FakeProvider([
            ProviderResponse(
                tool_calls=[_make_tool_call("c1", "file_write", '{"path": "x.py", "content": "new"}')],
            ),
            # Loop continues after blocked tool, provider completes
            ProviderResponse(text="Cannot write files.", finish_reason="stop"),
        ])
        adapter = LLMExecutionAdapter(provider)
        result = adapter.execute("op-1", {"summary": "write file"}, {})

        # file_write is prohibited, so it's blocked. The loop continues
        # and the provider returns text. The result is completed, NOT suspended.
        assert result["status"] == "completed"

    def test_execute_unknown_tool_rejected(self) -> None:
        """Unknown tool not in allowed_tools is blocked by policy."""
        provider = FakeProvider([
            ProviderResponse(
                tool_calls=[_make_tool_call("c1", "unknown_tool", '{}')],
            ),
            ProviderResponse(text="done", finish_reason="stop"),
        ])
        adapter = LLMExecutionAdapter(provider)
        result = adapter.execute("op-1", {"summary": "test"}, {})
        # Unknown tool is blocked; loop continues to completion
        assert result["status"] == "completed"

    def test_read_only_tools_no_approval(self) -> None:
        """file_read and list_directory do not require approval."""
        provider = FakeProvider([
            ProviderResponse(
                tool_calls=[_make_tool_call("c1", "file_read", '{"path": "src/app.py"}')],
            ),
        ])
        adapter = LLMExecutionAdapter(provider)
        result = adapter.execute("op-1", {"summary": "read file"}, {})

        # file_read suspends for frontend execution, NOT for approval
        assert result["status"] == "suspended"
        assert result["suspend_state"] == "suspended_for_tool_result"
        assert result["reason"] == "execution_tool_execution"


def test_file_write_absent_from_execution_provider_schemas() -> None:
    """file_write is prohibited — must not appear in execution tool schemas.

    Proves that ToolLoop.build_tool_schemas() correctly excludes file_write
    when using the execution adapter configuration.
    """
    from operation_controller.adapters.execution import (
        EXECUTION_TOOLS,
        EXECUTION_PROHIBITED_TOOLS,
    )

    config = ToolLoopConfig(
        allowed_tools=EXECUTION_TOOLS,
        prohibited_tools=EXECUTION_PROHIBITED_TOOLS,
        mutation_allowed=True,
    )
    loop = ToolLoop(config, FakeProvider([]))

    # Build schemas from standard tool definitions
    from operation_controller.tool_contracts import all_canonical_tools
    tool_defs = [
        {"name": t["name"], "description": t["description"], "parameters": t["parameters"]}
        for t in all_canonical_tools().values()
    ]
    schemas = loop.build_tool_schemas(tool_defs)
    names = {s["function"]["name"] for s in schemas}

    # file_write must NOT be in the schemas
    assert "file_write" not in names, (
        f"file_write must be excluded from execution schemas, got: {names}"
    )
    # But allowed tools should be present
    assert "file_read" in names
    assert "shell_exec" in names
