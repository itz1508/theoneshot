"""Tests for the shared ToolLoop utility.

Covers: config policy enforcement, loop execution (completion, failure modes),
tool-call routing (backend/frontend/approval), suspension/resume cycle,
cancellation, timeout, loop limits, and usage accumulation.
"""
from __future__ import annotations

import time
from threading import Event
from typing import Any

import pytest

from operation_controller.tool_loop import (
    BackendToolExecutor,
    LoopContinuation,
    PendingToolCall,
    ProviderResponse,
    ToolExecutionResult,
    ToolLoop,
    ToolLoopCompleted,
    ToolLoopConfig,
    ToolLoopFailed,
    ToolLoopProvider,
    ToolLoopSuspendedForApproval,
    ToolLoopSuspendedForTool,
    ToolTraceEntry,
    UsageRecord,
)


# ─── Fakes ────────────────────────────────────────────────────────────────────


class FakeProvider:
    """Scripted provider returning pre-configured responses in order."""

    def __init__(self, responses: list[ProviderResponse]) -> None:
        self._responses = list(responses)
        self._call_count = 0
        self.calls: list[dict[str, Any]] = []

    def call(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        model: str | None,
        max_tokens: int,
        timeout_seconds: float,
    ) -> ProviderResponse:
        self.calls.append({
            "messages": messages,
            "tools": tools,
            "model": model,
            "max_tokens": max_tokens,
        })
        if self._call_count >= len(self._responses):
            return ProviderResponse(text="exhausted", finish_reason="stop")
        resp = self._responses[self._call_count]
        self._call_count += 1
        return resp


class SlowProvider:
    """Provider that sleeps, used to test timeout."""

    def __init__(self, sleep_seconds: float) -> None:
        self._sleep = sleep_seconds
        self._call_count = 0

    def call(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        model: str | None,
        max_tokens: int,
        timeout_seconds: float,
    ) -> ProviderResponse:
        time.sleep(self._sleep)
        self._call_count += 1
        # Return a backend tool call so the loop iterates again (hitting timeout)
        return ProviderResponse(
            tool_calls=[_make_tool_call(f"slow-{self._call_count}", "audisor_scan", "{}")],
        )


class ErrorProvider:
    """Provider that raises an exception."""

    def __init__(self, error: Exception) -> None:
        self._error = error

    def call(self, **kwargs) -> ProviderResponse:
        raise self._error


class FakeExecutor:
    """Backend tool executor that records calls and returns scripted results."""

    def __init__(self, results: dict[str, ToolExecutionResult] | None = None) -> None:
        self._results = results or {}
        self.calls: list[tuple[str, dict]] = []

    def execute(self, tool_name: str, arguments: dict[str, Any]) -> ToolExecutionResult:
        self.calls.append((tool_name, arguments))
        return self._results.get(tool_name, ToolExecutionResult(output=f"{tool_name} output"))


# ─── Helpers ──────────────────────────────────────────────────────────────────


TOOL_DEFS = [
    {"name": "file_read", "description": "Read a file", "parameters": {}},
    {"name": "file_write", "description": "Write a file", "parameters": {}},
    {"name": "list_directory", "description": "List directory", "parameters": {}},
    {"name": "shell_exec", "description": "Execute shell", "parameters": {}},
    {"name": "audisor_scan", "description": "Scan code", "parameters": {}},
    {"name": "secret_tool", "description": "Not allowed", "parameters": {}},
]


def _make_tool_call(call_id: str, name: str, args: str = "{}") -> dict:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": args},
    }


# ─── Config policy tests ─────────────────────────────────────────────────────


class TestToolLoopConfig:
    def test_allowed_tools_filtering(self) -> None:
        config = ToolLoopConfig(allowed_tools=frozenset({"file_read", "audisor_scan"}))
        loop = ToolLoop(config, FakeProvider([]))
        schemas = loop.build_tool_schemas(TOOL_DEFS)
        names = {s["function"]["name"] for s in schemas}
        assert names == {"file_read", "audisor_scan"}

    def test_prohibited_overrides_allowed(self) -> None:
        config = ToolLoopConfig(
            allowed_tools=frozenset({"file_read", "audisor_scan"}),
            prohibited_tools=frozenset({"audisor_scan"}),
        )
        loop = ToolLoop(config, FakeProvider([]))
        schemas = loop.build_tool_schemas(TOOL_DEFS)
        names = {s["function"]["name"] for s in schemas}
        assert names == {"file_read"}

    def test_requires_approval_flag(self) -> None:
        config = ToolLoopConfig(
            allowed_tools=frozenset({"file_write", "shell_exec", "file_read"}),
            approval_required_tools=frozenset({"file_write", "shell_exec"}),
        )
        assert config.requires_approval("file_write") is True
        assert config.requires_approval("shell_exec") is True
        assert config.requires_approval("file_read") is False


# ─── Loop execution tests ────────────────────────────────────────────────────


class TestToolLoopExecution:
    def test_no_tool_calls_returns_completed(self) -> None:
        config = ToolLoopConfig(allowed_tools=frozenset({"file_read"}))
        provider = FakeProvider([
            ProviderResponse(text="Here is my answer", finish_reason="stop",
                             usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}),
        ])
        loop = ToolLoop(config, provider)
        outcome = loop.run([{"role": "user", "content": "hi"}], [])

        assert isinstance(outcome, ToolLoopCompleted)
        assert outcome.reply == "Here is my answer"
        assert outcome.finish_reason == "stop"
        assert len(outcome.usage) == 1
        assert outcome.usage[0].prompt_tokens == 10

    def test_backend_tool_executed_inline(self) -> None:
        """Backend tool (audisor_scan) is executed locally; loop continues to completion."""
        config = ToolLoopConfig(allowed_tools=frozenset({"audisor_scan", "file_read"}))
        executor = FakeExecutor({"audisor_scan": ToolExecutionResult(output="scan result")})
        provider = FakeProvider([
            # First call: requests audisor_scan
            ProviderResponse(
                tool_calls=[_make_tool_call("c1", "audisor_scan", '{"path": "/src"}')],
                usage={"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
            ),
            # Second call: returns completion
            ProviderResponse(text="Done scanning", finish_reason="stop",
                             usage={"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30}),
        ])
        loop = ToolLoop(config, provider, executor)
        outcome = loop.run([{"role": "user", "content": "scan"}], [])

        assert isinstance(outcome, ToolLoopCompleted)
        assert outcome.reply == "Done scanning"
        assert len(executor.calls) == 1
        assert executor.calls[0] == ("audisor_scan", {"path": "/src"})
        # Usage from both calls
        assert len(outcome.usage) == 2

    def test_frontend_tool_suspends(self) -> None:
        """Frontend tool (file_read) causes suspension."""
        config = ToolLoopConfig(allowed_tools=frozenset({"file_read", "audisor_scan"}))
        provider = FakeProvider([
            ProviderResponse(
                tool_calls=[_make_tool_call("c1", "file_read", '{"path": "main.py"}')],
                usage={"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
            ),
        ])
        loop = ToolLoop(config, provider)
        outcome = loop.run([{"role": "user", "content": "read file"}], [])

        assert isinstance(outcome, ToolLoopSuspendedForTool)
        assert len(outcome.pending_calls) == 1
        assert outcome.pending_calls[0].call_id == "c1"
        assert outcome.pending_calls[0].tool_name == "file_read"
        assert outcome.pending_calls[0].arguments == {"path": "main.py"}
        assert outcome.continuation is not None

    def test_approval_required_suspends(self) -> None:
        """Tool requiring approval suspends for approval."""
        config = ToolLoopConfig(
            allowed_tools=frozenset({"file_write", "file_read"}),
            mutation_allowed=True,
            approval_required_tools=frozenset({"file_write"}),
        )
        provider = FakeProvider([
            ProviderResponse(
                tool_calls=[_make_tool_call("c1", "file_write", '{"path": "x.py", "content": "hi"}')],
            ),
        ])
        loop = ToolLoop(config, provider)
        outcome = loop.run([{"role": "user", "content": "write"}], [])

        assert isinstance(outcome, ToolLoopSuspendedForApproval)
        assert outcome.tool_call.call_id == "c1"
        assert outcome.tool_call.tool_name == "file_write"
        assert "approval" in outcome.reason.lower()
        assert outcome.risk_level == "medium"  # mutation_allowed=True

    def test_disallowed_tool_blocked(self) -> None:
        """Tool not in allowed_tools is blocked; loop continues."""
        config = ToolLoopConfig(allowed_tools=frozenset({"file_read"}))
        provider = FakeProvider([
            # First: requests disallowed tool
            ProviderResponse(
                tool_calls=[_make_tool_call("c1", "secret_tool", "{}")],
            ),
            # Second: completes
            ProviderResponse(text="ok", finish_reason="stop"),
        ])
        loop = ToolLoop(config, provider)
        outcome = loop.run([{"role": "user", "content": "go"}], [])

        assert isinstance(outcome, ToolLoopCompleted)
        # Tool trace should show the blocked tool
        blocked = [t for t in outcome.tool_trace if t.status == "failed" and "blocked" in t.executor]
        assert len(blocked) == 1
        assert blocked[0].tool_name == "secret_tool"

    def test_cancellation_via_abort_event(self) -> None:
        config = ToolLoopConfig(allowed_tools=frozenset({"file_read"}))
        provider = FakeProvider([ProviderResponse(text="should not reach")])
        abort = Event()
        abort.set()  # Pre-set

        loop = ToolLoop(config, provider)
        outcome = loop.run([{"role": "user", "content": "x"}], [], abort=abort)

        assert isinstance(outcome, ToolLoopFailed)
        assert outcome.error == "cancelled"

    def test_timeout(self) -> None:
        config = ToolLoopConfig(
            allowed_tools=frozenset({"audisor_scan"}),
            timeout_seconds=0.05,
            max_iterations=10,
        )
        executor = FakeExecutor()
        provider = SlowProvider(sleep_seconds=0.06)
        loop = ToolLoop(config, provider, executor)
        outcome = loop.run([{"role": "user", "content": "x"}], [])

        assert isinstance(outcome, ToolLoopFailed)
        assert "timeout" in outcome.error

    def test_loop_limit_exceeded(self) -> None:
        """Provider always returns tool calls — hits loop limit."""
        config = ToolLoopConfig(
            allowed_tools=frozenset({"audisor_scan"}),
            max_iterations=2,
        )
        executor = FakeExecutor()
        # Always returns a backend tool call so loop continues
        provider = FakeProvider([
            ProviderResponse(tool_calls=[_make_tool_call(f"c{i}", "audisor_scan", "{}")])
            for i in range(5)
        ])
        loop = ToolLoop(config, provider, executor)
        outcome = loop.run([{"role": "user", "content": "go"}], [])

        assert isinstance(outcome, ToolLoopFailed)
        assert "loop_limit" in outcome.error

    def test_provider_exception(self) -> None:
        config = ToolLoopConfig(allowed_tools=frozenset({"file_read"}))
        provider = ErrorProvider(RuntimeError("connection refused"))
        loop = ToolLoop(config, provider)
        outcome = loop.run([{"role": "user", "content": "x"}], [])

        assert isinstance(outcome, ToolLoopFailed)
        assert "provider_error" in outcome.error
        assert "connection refused" in outcome.error


# ─── Suspension/resume tests ────────────────────────────────────────────────


class TestToolLoopSuspensionResume:
    def test_suspend_continuation_roundtrip(self) -> None:
        """Suspend -> serialize -> deserialize -> resume -> complete."""
        config = ToolLoopConfig(allowed_tools=frozenset({"file_read"}))

        # First call: suspends for file_read
        provider1 = FakeProvider([
            ProviderResponse(
                tool_calls=[_make_tool_call("c1", "file_read", '{"path": "a.py"}')],
                usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            ),
        ])
        loop1 = ToolLoop(config, provider1)
        outcome1 = loop1.run(
            [{"role": "user", "content": "read a.py"}],
            [],
        )
        assert isinstance(outcome1, ToolLoopSuspendedForTool)

        # Serialize/deserialize continuation
        serialized = outcome1.continuation.serialize()
        continuation = LoopContinuation.deserialize(serialized)

        # Resume: provider returns completion
        provider2 = FakeProvider([
            ProviderResponse(text="File contains: hello world", finish_reason="stop",
                             usage={"prompt_tokens": 20, "completion_tokens": 8, "total_tokens": 28}),
        ])
        loop2 = ToolLoop(config, provider2)
        outcome2 = loop2.resume(
            continuation,
            tool_results=[{
                "call_id": "c1",
                "tool_name": "file_read",
                "output": "hello world",
                "error": None,
                "status": "success",
            }],
        )

        assert isinstance(outcome2, ToolLoopCompleted)
        assert "hello world" in outcome2.reply

    def test_resume_appends_tool_results_to_messages(self) -> None:
        """After resume, the message buffer contains the tool result."""
        config = ToolLoopConfig(allowed_tools=frozenset({"file_read"}))
        provider1 = FakeProvider([
            ProviderResponse(
                tool_calls=[_make_tool_call("c1", "file_read", '{"path": "x.py"}')],
            ),
        ])
        loop1 = ToolLoop(config, provider1)
        outcome1 = loop1.run([{"role": "user", "content": "go"}], [])
        assert isinstance(outcome1, ToolLoopSuspendedForTool)

        # Resume with a provider that we can inspect messages on
        provider2 = FakeProvider([
            ProviderResponse(text="done", finish_reason="stop"),
        ])
        loop2 = ToolLoop(config, provider2)
        loop2.resume(
            outcome1.continuation,
            tool_results=[{
                "call_id": "c1",
                "tool_name": "file_read",
                "output": "file contents here",
                "error": None,
                "status": "success",
            }],
        )

        # Inspect what messages the provider received
        assert len(provider2.calls) == 1
        messages = provider2.calls[0]["messages"]
        # Should contain a tool result message
        tool_msgs = [m for m in messages if m.get("role") == "tool"]
        assert len(tool_msgs) >= 1
        assert tool_msgs[0]["tool_call_id"] == "c1"
        assert tool_msgs[0]["content"] == "file contents here"

    def test_usage_accumulates_across_resume(self) -> None:
        """Usage records from both segments are present in the final outcome."""
        config = ToolLoopConfig(allowed_tools=frozenset({"file_read"}))

        # First run: one provider call with usage
        provider1 = FakeProvider([
            ProviderResponse(
                tool_calls=[_make_tool_call("c1", "file_read", "{}")],
                usage={"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
            ),
        ])
        loop1 = ToolLoop(config, provider1)
        outcome1 = loop1.run([{"role": "user", "content": "x"}], [])
        assert isinstance(outcome1, ToolLoopSuspendedForTool)
        # First segment has usage
        assert len(outcome1.usage) == 1
        assert outcome1.usage[0].prompt_tokens == 100

        # Resume: another provider call with different usage
        provider2 = FakeProvider([
            ProviderResponse(
                text="final answer",
                usage={"prompt_tokens": 200, "completion_tokens": 80, "total_tokens": 280},
            ),
        ])
        loop2 = ToolLoop(config, provider2)
        outcome2 = loop2.resume(
            outcome1.continuation,
            tool_results=[{"call_id": "c1", "tool_name": "file_read", "output": "x", "status": "success"}],
        )

        assert isinstance(outcome2, ToolLoopCompleted)
        # Resume segment has its own usage
        assert len(outcome2.usage) == 1
        assert outcome2.usage[0].prompt_tokens == 200
