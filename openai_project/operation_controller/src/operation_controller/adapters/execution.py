"""ExecutionAdapter — uses shared ToolLoop with mutation-capable tools.

Executes a reviewed plan using the tool loop with write access and
approval requirements for dangerous operations.
"""
from __future__ import annotations

import json
import uuid
from typing import Any

from operation_controller.tool_loop import (
    BackendToolExecutor,
    LoopContinuation,
    ToolLoop,
    ToolLoopCompleted,
    ToolLoopConfig,
    ToolLoopFailed,
    ToolLoopProvider,
    ToolLoopSuspendedForApproval,
    ToolLoopSuspendedForTool,
)


# Tools available during execution (file_write is PROHIBITED this milestone).
EXECUTION_TOOLS = frozenset({
    "file_read", "list_directory", "shell_exec", "audisor_scan",
})

# Tools explicitly prohibited — model requests are rejected by policy.
EXECUTION_PROHIBITED_TOOLS = frozenset({"file_write"})

# Tools that require operator approval before execution
APPROVAL_REQUIRED_TOOLS = frozenset({"shell_exec"})

EXECUTION_SYSTEM_PROMPT = """\
You are a coding assistant executing an approved implementation plan. \
You have access to tools that can read and write files, and run commands. \
Follow the plan precisely. Report your progress and any issues encountered.

After completing all steps, summarize what was done.
"""


class LLMExecutionAdapter:
    """ExecutionAdapter that uses the shared ToolLoop with mutation tools."""

    def __init__(
        self,
        provider: ToolLoopProvider,
        backend_executor: BackendToolExecutor | None = None,
        tool_definitions: list[dict[str, Any]] | None = None,
    ) -> None:
        self._provider = provider
        self._executor = backend_executor
        self._tool_definitions = tool_definitions or []

    def execute(
        self, operation_id: str, plan: dict[str, Any], authority: dict[str, Any]
    ) -> dict[str, Any]:
        """Execute the plan using the tool loop.

        Returns:
            {"status": "completed", "output": "..."} on success.
            {"status": "suspended", ...} when frontend tools or approval needed.
            {"status": "failed", "error": "..."} on failure.
        """
        config = ToolLoopConfig(
            allowed_tools=EXECUTION_TOOLS,
            prohibited_tools=EXECUTION_PROHIBITED_TOOLS,
            mutation_allowed=True,
            approval_required_tools=APPROVAL_REQUIRED_TOOLS,
            max_iterations=15,
            timeout_seconds=300.0,
            system_prompt=EXECUTION_SYSTEM_PROMPT,
        )

        loop = ToolLoop(config, self._provider, self._executor)
        tools = loop.build_tool_schemas(self._tool_definitions)

        # Build execution prompt including the plan
        plan_text = json.dumps(plan, indent=2) if isinstance(plan, dict) else str(plan)
        user_prompt = (
            f"Execute the following implementation plan:\n\n"
            f"```json\n{plan_text}\n```\n\n"
            f"Implement each step in order. Use the available tools to read and modify files."
        )

        messages = [
            {"role": "system", "content": EXECUTION_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

        outcome = loop.run(messages, tools)
        return self._interpret_outcome(outcome)

    def resume_execution(self, record: Any, resume_input: dict[str, Any]) -> dict[str, Any]:
        """Resume execution after tool results or approval."""
        continuation_data = record.artifacts.get("continuation")
        if not continuation_data:
            return {"status": "failed", "error": "no continuation data found"}

        continuation = LoopContinuation.deserialize(continuation_data)
        tool_results = resume_input.get("tool_results", [])

        config = ToolLoopConfig(
            allowed_tools=EXECUTION_TOOLS,
            prohibited_tools=EXECUTION_PROHIBITED_TOOLS,
            mutation_allowed=True,
            approval_required_tools=APPROVAL_REQUIRED_TOOLS,
            max_iterations=15,
            timeout_seconds=300.0,
            system_prompt=EXECUTION_SYSTEM_PROMPT,
        )

        loop = ToolLoop(config, self._provider, self._executor)
        outcome = loop.resume(continuation, tool_results)
        return self._interpret_outcome(outcome)

    def _interpret_outcome(self, outcome) -> dict[str, Any]:
        """Convert ToolLoopOutcome to adapter result dict."""
        if isinstance(outcome, ToolLoopCompleted):
            return {
                "status": "completed",
                "output": outcome.reply,
                "usage": [
                    {
                        "prompt_tokens": u.prompt_tokens,
                        "completion_tokens": u.completion_tokens,
                        "total_tokens": u.total_tokens,
                    }
                    for u in outcome.usage
                ],
            }

        elif isinstance(outcome, ToolLoopFailed):
            return {"status": "failed", "error": outcome.error}

        elif isinstance(outcome, ToolLoopSuspendedForTool):
            suspension_id = f"susp-{uuid.uuid4().hex[:12]}"
            return {
                "status": "suspended",
                "suspend_state": "suspended_for_tool_result",
                "reason": "execution_tool_execution",
                "suspension_id": suspension_id,
                "pending_calls": [
                    {
                        "call_id": c.call_id,
                        "tool_name": c.tool_name,
                        "arguments": c.arguments,
                    }
                    for c in outcome.pending_calls
                ],
                "continuation": outcome.continuation.serialize(),
            }

        elif isinstance(outcome, ToolLoopSuspendedForApproval):
            suspension_id = f"susp-{uuid.uuid4().hex[:12]}"
            return {
                "status": "suspended",
                "suspend_state": "suspended_for_approval",
                "reason": outcome.reason,
                "risk_level": outcome.risk_level,
                "suspension_id": suspension_id,
                "tool_call": {
                    "call_id": outcome.tool_call.call_id,
                    "tool_name": outcome.tool_call.tool_name,
                    "arguments": outcome.tool_call.arguments,
                },
                "continuation": outcome.continuation.serialize(),
            }

        return {"status": "failed", "error": "unknown_outcome_type"}
