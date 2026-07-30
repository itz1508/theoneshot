"""PlanningAdapter — uses shared ToolLoop with read-only tools.

Constructs a planning system prompt, invokes ToolLoop, and interprets
the outcome for the controller.
"""
from __future__ import annotations

import json
import uuid
from typing import Any

from operation_controller.tool_loop import (
    BackendToolExecutor,
    LoopContinuation,
    ToolExecutionResult,
    ToolLoop,
    ToolLoopCompleted,
    ToolLoopConfig,
    ToolLoopFailed,
    ToolLoopProvider,
    ToolLoopSuspendedForApproval,
    ToolLoopSuspendedForTool,
)


# Read-only tools available during planning.
# shell_exec is allowed but requires approval (it can mutate via commands).
PLANNING_TOOLS = frozenset({"file_read", "list_directory", "shell_exec", "audisor_scan"})

# Tools that require operator approval before execution during planning
PLANNING_APPROVAL_REQUIRED = frozenset({"shell_exec"})

PLANNING_SYSTEM_PROMPT = """\
You are a coding assistant in the planning phase. You MUST use the available \
tools to inspect the codebase before creating your plan. Do NOT output a plan \
until you have gathered information by calling tools.

IMPORTANT RULES:
1. You MUST call at least one tool (file_read, list_directory, or shell_exec) \
BEFORE generating your plan. Start by reading relevant files or listing directories.
2. Do NOT modify any files — read-only inspection only.
3. After inspecting the codebase, output your plan as a JSON object with:
   - "summary": brief description of what needs to be done
   - "steps": list of implementation steps, each with "description" and "files"
   - "risks": any risks or concerns
4. Wrap the final plan in ```json ... ``` markers.

Always start by calling a tool. Never skip tool use.
"""


class LLMPlanningAdapter:
    """PlanningAdapter that uses the shared ToolLoop with read-only tools."""

    def __init__(
        self,
        provider: ToolLoopProvider,
        backend_executor: BackendToolExecutor | None = None,
        tool_definitions: list[dict[str, Any]] | None = None,
    ) -> None:
        self._provider = provider
        self._executor = backend_executor
        self._tool_definitions = tool_definitions or []

    def create_plan(self, prompt: str, context: dict[str, Any]) -> dict[str, Any]:
        """Create a plan using the tool loop.

        Returns:
            {"status": "completed", "plan": {...}} on success.
            {"status": "suspended", ...} when frontend tools are needed.
            {"status": "failed", "error": "..."} on failure.
        """
        config = ToolLoopConfig(
            allowed_tools=PLANNING_TOOLS,
            mutation_allowed=False,
            approval_required_tools=PLANNING_APPROVAL_REQUIRED,
            max_iterations=10,
            timeout_seconds=180.0,
            system_prompt=PLANNING_SYSTEM_PROMPT,
        )

        loop = ToolLoop(config, self._provider, self._executor)
        tools = loop.build_tool_schemas(self._tool_definitions)

        messages = [
            {"role": "system", "content": PLANNING_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]

        outcome = loop.run(messages, tools)
        return self._interpret_outcome(outcome)

    def resume_plan(self, record: Any, resume_input: dict[str, Any]) -> dict[str, Any]:
        """Resume plan creation after tool results arrive."""
        continuation_data = record.artifacts.get("continuation")
        if not continuation_data:
            return {"status": "failed", "error": "no continuation data found"}

        continuation = LoopContinuation.deserialize(continuation_data)

        # Extract tool results from resume_input
        tool_results = resume_input.get("tool_results", [])

        config = ToolLoopConfig(
            allowed_tools=PLANNING_TOOLS,
            mutation_allowed=False,
            approval_required_tools=PLANNING_APPROVAL_REQUIRED,
            max_iterations=10,
            timeout_seconds=180.0,
            system_prompt=PLANNING_SYSTEM_PROMPT,
        )

        loop = ToolLoop(config, self._provider, self._executor)
        outcome = loop.resume(continuation, tool_results)
        return self._interpret_outcome(outcome)

    def _interpret_outcome(self, outcome) -> dict[str, Any]:
        """Convert ToolLoopOutcome to adapter result dict."""
        if isinstance(outcome, ToolLoopCompleted):
            plan = self._parse_plan(outcome.reply)
            return {
                "status": "completed",
                "plan": plan,
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
                "reason": "planning_tool_execution",
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

    def _parse_plan(self, reply: str) -> dict[str, Any]:
        """Extract plan JSON from the model's reply text."""
        # Try to find JSON block
        import re
        match = re.search(r"```json\s*\n(.*?)\n\s*```", reply, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass

        # Try direct JSON parse
        try:
            return json.loads(reply)
        except json.JSONDecodeError:
            pass

        # Fallback: wrap the text as a simple plan
        return {
            "summary": reply[:500],
            "steps": [],
            "risks": ["Could not parse structured plan from model output"],
            "raw_reply": reply,
        }
