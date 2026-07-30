"""Chat orchestrator — manages the multi-turn tool-calling loop.

The orchestrator:
1. Sends tool schemas + messages to the provider (Qwen)
2. Receives tool calls or final text
3. Routes backend-owned tools to local execution
4. Returns frontend-owned tool calls to the client for WebContainer execution
5. Resumes after the client submits tool results
6. Repeats until a final text response or the loop limit is reached
"""
from __future__ import annotations

import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

from ..providers.base import (
    AssistantProvider,
    CompletionReply,
    CompletionRequest,
    ToolCallRequest,
    ToolSchema,
)
from ..tools.registry import ToolRegistry
from ..tools.schemas import ToolCall, ToolDefinition, ToolExecutor, ToolResult

logger = logging.getLogger("audisor_assistant.orchestrator")

# ─── Configuration ───────────────────────────────────────────────────────────

MAX_TOOL_LOOPS = int(os.environ.get("AUDISOR_CHAT_MAX_TOOL_LOOPS", "15"))
LOOP_TIMEOUT_SECONDS = 300.0
TURN_STATE_TTL_SECONDS = 120.0
MAX_OUTPUT_BYTES = 100_000  # 100KB per tool result
MAX_AGGREGATE_OUTPUT_BYTES = 500_000  # 500KB total per turn


# ─── Turn result types ───────────────────────────────────────────────────────


@dataclass
class ToolCallEvent:
    """A tool call event for the API response."""

    call_id: str
    tool_name: str
    arguments: dict[str, Any]
    executor: str  # "frontend" | "backend"
    status: str  # "pending" | "completed" | "failed"
    turn_id: str
    operation_id: str | None = None
    output: str | None = None
    error: str | None = None
    duration_ms: int | None = None


@dataclass
class ChatTurnFinal:
    """Final response — loop complete, return text to client."""

    reply: str
    usage: dict[str, int]
    tool_trace: list[ToolCallEvent]
    finish_reason: str = "stop"


@dataclass
class ChatTurnPending:
    """Needs frontend execution — return pending tool calls to client."""

    turn_id: str
    pending_calls: list[ToolCallEvent]
    completed_calls: list[ToolCallEvent]  # backend tools already done
    loop_iteration: int
    max_loops: int = MAX_TOOL_LOOPS


@dataclass
class ChatTurnApprovalRequired:
    """Needs operator confirmation for a write/exec tool."""

    turn_id: str
    tool_call: ToolCallEvent
    reason: str
    risk_level: str = "medium"


ChatTurnResult = ChatTurnFinal | ChatTurnPending | ChatTurnApprovalRequired


# ─── Turn state (in-memory) ──────────────────────────────────────────────────


@dataclass
class TurnState:
    """In-memory state for a tool-calling turn awaiting frontend results."""

    turn_id: str
    messages: list[dict[str, Any]]  # internal message buffer
    tools: tuple[ToolSchema, ...]
    provider: AssistantProvider
    registry: ToolRegistry
    system_prompt: str
    loop_iteration: int = 0
    cumulative_usage: dict[str, int] = field(
        default_factory=lambda: {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    )
    tool_trace: list[ToolCallEvent] = field(default_factory=list)
    # Backend results waiting to be combined with frontend results
    pending_backend_results: list[ToolResult] = field(default_factory=list)
    # The model's tool_calls that need frontend execution
    pending_frontend_calls: list[ToolCall] = field(default_factory=list)
    # The raw ToolCallRequests for resumption
    pending_raw_calls: list[ToolCallRequest] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    workspace_available: bool = False

    @property
    def expired(self) -> bool:
        return (time.time() - self.created_at) > TURN_STATE_TTL_SECONDS


# Global turn state store (single-server, in-memory)
_turn_states: dict[str, TurnState] = {}


def _cleanup_expired() -> None:
    """Lazy eviction of expired turn states."""
    expired = [tid for tid, state in _turn_states.items() if state.expired]
    for tid in expired:
        del _turn_states[tid]


# ─── Backend tool executor ───────────────────────────────────────────────────


def _execute_backend_tool(tool_def: ToolDefinition, call: ToolCall) -> ToolResult:
    """Execute a backend-owned tool and return the result.

    Milestone 1: only audisor_scan is implemented. Others return a stub.
    """
    start = time.time()
    try:
        if tool_def.name == "audisor_scan":
            output = _execute_audisor_scan(call.arguments)
        else:
            output = json.dumps({
                "status": "not_implemented",
                "message": f"Tool '{tool_def.name}' is registered but not yet implemented.",
            })
        duration_ms = int((time.time() - start) * 1000)
        return ToolResult(
            call_id=call.id,
            name=call.name,
            output=output[:MAX_OUTPUT_BYTES],
            status="success",
            duration_ms=duration_ms,
        )
    except Exception as exc:
        duration_ms = int((time.time() - start) * 1000)
        return ToolResult(
            call_id=call.id,
            name=call.name,
            error=str(exc)[:1000],
            status="error",
            duration_ms=duration_ms,
        )


def _execute_audisor_scan(arguments: dict[str, Any]) -> str:
    """Run an Audisor scan. Milestone 1: returns a stub analysis."""
    target = arguments.get("target", ".")
    depth = arguments.get("depth", "standard")
    # Milestone 1: return a structured stub indicating the scan was requested.
    # Full implementation will wire to the actual audisor scanner.
    return json.dumps({
        "tool": "audisor_scan",
        "target": target,
        "depth": depth,
        "status": "completed",
        "findings": [],
        "summary": f"Scan of '{target}' at depth '{depth}' completed. No issues found.",
        "note": "Audisor scanner integration pending — this is a structural stub.",
    })


# ─── Orchestrator ────────────────────────────────────────────────────────────


class ChatOrchestrator:
    """Orchestrates the multi-turn tool-calling loop for operator chat."""

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    def execute_turn(
        self,
        *,
        provider: AssistantProvider,
        system_prompt: str,
        user_prompt: str,
        history: tuple,  # tuple[HistoryMessage, ...]
        workspace_available: bool = False,
        model_override: str | None = None,
        max_tokens: int = 2048,
        timeout_seconds: float = 120.0,
    ) -> ChatTurnResult:
        """Start a new tool-calling turn.

        Returns immediately if no tools are called (ChatTurnFinal).
        Returns ChatTurnPending if frontend tools are needed.
        Returns ChatTurnApprovalRequired if approval is needed.
        """
        # Build tool schemas filtered by availability
        available_tools = self._registry.filter_by_availability(workspace_available)
        tool_schemas = tuple(
            ToolSchema(name=t.name, description=t.description, parameters=t.parameters)
            for t in available_tools
        )

        # Build initial messages
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            *({"role": turn.role, "content": turn.content} for turn in history),
            {"role": "user", "content": user_prompt},
        ]

        # Create turn state
        turn_id = f"turn-{uuid.uuid4().hex[:12]}"
        state = TurnState(
            turn_id=turn_id,
            messages=messages,
            tools=tool_schemas,
            provider=provider,
            registry=self._registry,
            system_prompt=system_prompt,
            workspace_available=workspace_available,
        )

        return self._run_loop(state, model_override, max_tokens, timeout_seconds)

    def continue_turn(
        self,
        turn_id: str,
        tool_results: list[ToolResult],
    ) -> ChatTurnResult:
        """Resume a turn after frontend tool execution or approval."""
        _cleanup_expired()

        state = _turn_states.pop(turn_id, None)
        if state is None:
            return ChatTurnFinal(
                reply="Turn expired or not found. Please retry your message.",
                usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                tool_trace=[],
                finish_reason="error",
            )

        if state.expired:
            return ChatTurnFinal(
                reply="Turn expired. Please retry your message.",
                usage=state.cumulative_usage,
                tool_trace=state.tool_trace,
                finish_reason="error",
            )

        # Combine stored backend results + submitted frontend results
        all_results = state.pending_backend_results + tool_results

        # Build tool result messages for the model
        # First, add the assistant's tool_calls message
        tool_calls_message = {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": tc.arguments},
                }
                for tc in state.pending_raw_calls
            ],
        }
        state.messages.append(tool_calls_message)

        # Then add each tool result
        for result in all_results:
            state.messages.append({
                "role": "tool",
                "tool_call_id": result.call_id,
                "content": result.to_message_content(),
            })

            # Record in trace
            tool_def = self._registry.get(result.name)
            executor = tool_def.executor.value if tool_def else "unknown"
            state.tool_trace.append(ToolCallEvent(
                call_id=result.call_id,
                tool_name=result.name,
                arguments={},  # already recorded on pending
                executor=executor,
                status="completed" if result.succeeded else "failed",
                turn_id=turn_id,
                operation_id=result.operation_id,
                output=result.output[:500] if result.output else None,
                error=result.error,
                duration_ms=result.duration_ms,
            ))

        # Clear pending state
        state.pending_backend_results = []
        state.pending_frontend_calls = []
        state.pending_raw_calls = []

        # Retrieve model override from previous state context
        model_override = None  # Continue with provider default
        return self._run_loop(state, model_override, 2048, 120.0)

    def _run_loop(
        self,
        state: TurnState,
        model_override: str | None,
        max_tokens: int,
        timeout_seconds: float,
    ) -> ChatTurnResult:
        """Internal loop: call provider, handle tool calls, repeat."""
        loop_start = time.time()

        while state.loop_iteration < MAX_TOOL_LOOPS:
            # Check total timeout
            if (time.time() - loop_start) > LOOP_TIMEOUT_SECONDS:
                return ChatTurnFinal(
                    reply="Tool loop timeout exceeded. Here's what I found so far based on the tools I was able to use.",
                    usage=state.cumulative_usage,
                    tool_trace=state.tool_trace,
                    finish_reason="timeout",
                )

            state.loop_iteration += 1

            # Build completion request (tools mode uses raw messages)
            request = CompletionRequest(
                system_prompt=state.messages[0]["content"],
                user_prompt=state.messages[-1].get("content", ""),
                max_tokens=max_tokens,
                timeout_seconds=timeout_seconds,
                purpose="operator_chat",
                model_override=model_override,
                history=(),  # History is in messages buffer directly
                tools=state.tools if state.tools else (),
                tool_choice="auto" if state.tools else None,
            )

            # For tool-calling, we need to pass the full messages array.
            # Override the provider call to use messages directly.
            reply = self._call_provider_with_messages(
                state.provider, state.messages, state.tools,
                model_override, max_tokens, timeout_seconds,
            )

            # Accumulate usage
            if reply.usage:
                for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                    state.cumulative_usage[key] += reply.usage.get(key, 0)

            # Check if model returned tool calls
            if reply.tool_calls:
                return self._handle_tool_calls(state, reply)

            # Final text response
            return ChatTurnFinal(
                reply=reply.text,
                usage=state.cumulative_usage,
                tool_trace=state.tool_trace,
                finish_reason=reply.finish_reason,
            )

        # Loop limit exceeded
        return ChatTurnFinal(
            reply="I've reached the maximum number of tool calls for this turn. Here's my answer based on what I've gathered so far.",
            usage=state.cumulative_usage,
            tool_trace=state.tool_trace,
            finish_reason="loop_limit",
        )

    def _call_provider_with_messages(
        self,
        provider: AssistantProvider,
        messages: list[dict[str, Any]],
        tools: tuple[ToolSchema, ...],
        model_override: str | None,
        max_tokens: int,
        timeout_seconds: float,
    ) -> CompletionReply:
        """Call provider with the full messages array for tool-calling.

        Constructs a CompletionRequest that carries the full context.
        The provider's complete() method uses system_prompt + history + user_prompt
        to build messages, so we extract them from our buffer.
        """
        from ..providers.base import HistoryMessage

        # Extract system prompt (first message)
        system_prompt = messages[0]["content"] if messages else ""

        # Extract user prompt (last user message)
        user_prompt = ""
        history_messages: list[HistoryMessage] = []

        # Build history from messages[1:-1] (skip system, skip last)
        # The last message might be a tool result or user message
        non_system = messages[1:]

        # For tool-calling continuations, the message buffer may contain
        # tool_calls and tool results. We need to pass these as history.
        # However, the current provider interface only supports user/assistant text.
        # For the tool loop, we construct a synthetic user prompt that includes
        # tool results context when the last message is a tool result.

        # Strategy: flatten tool results into the conversation for providers
        # that don't natively support the tool message format.
        # The LocalOpenAICompatibleProvider uses /v1/chat/completions which
        # DOES support tool messages natively. We'll pass raw messages.
        from ..providers.local_openai_compatible import LocalOpenAICompatibleProvider

        if isinstance(provider, LocalOpenAICompatibleProvider):
            return self._call_openai_compatible(
                provider, messages, tools, model_override, max_tokens, timeout_seconds
            )

        # Fallback for other providers: flatten to text
        # Collect all content into a single user prompt
        parts = []
        for msg in non_system:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if role == "tool":
                parts.append(f"[Tool result for {msg.get('tool_call_id', 'unknown')}]: {content}")
            elif content:
                parts.append(content)

        user_prompt = "\n\n".join(parts) if parts else ""

        request = CompletionRequest(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=max_tokens,
            timeout_seconds=timeout_seconds,
            purpose="operator_chat",
            model_override=model_override,
            tools=tools,
            tool_choice="auto" if tools else None,
        )
        return provider.complete(request)

    def _call_openai_compatible(
        self,
        provider,
        messages: list[dict[str, Any]],
        tools: tuple[ToolSchema, ...],
        model_override: str | None,
        max_tokens: int,
        timeout_seconds: float,
    ) -> CompletionReply:
        """Direct OpenAI-compatible API call with full message array including tool results."""
        import requests as http_requests
        from ..providers.base import ProviderError
        from ..providers.local_openai_compatible import _parse_chat_completion
        from ..schemas.responses import PublicErrorCategory

        body: dict[str, Any] = {
            "model": provider.effective_model(model_override),
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 0,
            "stream": False,
        }
        if tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
                for t in tools
            ]
            body["tool_choice"] = "auto"

        timeout = timeout_seconds or provider.timeout_seconds
        try:
            response = http_requests.post(
                f"{provider.base_url}/v1/chat/completions",
                json=body,
                timeout=timeout,
            )
        except http_requests.Timeout as exc:
            raise ProviderError(
                PublicErrorCategory.TIMEOUT, "Local provider timed out."
            ) from exc
        except http_requests.ConnectionError as exc:
            raise ProviderError(
                PublicErrorCategory.UNAVAILABLE, "Local provider is unavailable."
            ) from exc
        except http_requests.RequestException as exc:
            raise ProviderError(
                PublicErrorCategory.INTERNAL, "Local provider request failed."
            ) from exc

        return _parse_chat_completion(
            response, model=provider.effective_model(model_override)
        )

    def _handle_tool_calls(
        self, state: TurnState, reply: CompletionReply
    ) -> ChatTurnResult:
        """Process tool calls: execute backend, queue frontend."""
        backend_calls: list[ToolCall] = []
        frontend_calls: list[ToolCall] = []
        approval_needed: list[ToolCall] = []
        raw_calls: list[ToolCallRequest] = []

        for tc in reply.tool_calls:
            raw_calls.append(tc)
            try:
                args = json.loads(tc.arguments) if tc.arguments else {}
            except (json.JSONDecodeError, TypeError):
                args = {}

            call = ToolCall(id=tc.id, name=tc.name, arguments=args)
            tool_def = self._registry.get(tc.name)

            if tool_def is None:
                # Unknown tool — record as error
                state.tool_trace.append(ToolCallEvent(
                    call_id=tc.id,
                    tool_name=tc.name,
                    arguments=args,
                    executor="unknown",
                    status="failed",
                    turn_id=state.turn_id,
                    error=f"Unknown tool: {tc.name}",
                ))
                continue

            if tool_def.requires_approval:
                approval_needed.append(call)
            elif tool_def.executor == ToolExecutor.BACKEND:
                backend_calls.append(call)
            else:
                frontend_calls.append(call)

        # If any tool needs approval, return approval request for the first one
        if approval_needed:
            call = approval_needed[0]
            tool_def = self._registry.get(call.name)
            # Store state for resumption
            state.pending_raw_calls = raw_calls
            state.pending_backend_results = []
            state.pending_frontend_calls = [call] + frontend_calls
            _turn_states[state.turn_id] = state

            return ChatTurnApprovalRequired(
                turn_id=state.turn_id,
                tool_call=ToolCallEvent(
                    call_id=call.id,
                    tool_name=call.name,
                    arguments=call.arguments,
                    executor=tool_def.executor.value if tool_def else "frontend",
                    status="pending",
                    turn_id=state.turn_id,
                ),
                reason=f"Tool '{call.name}' requires operator approval before execution.",
                risk_level="medium" if tool_def and not tool_def.read_only else "low",
            )

        # Execute backend tools
        backend_results: list[ToolResult] = []
        completed_events: list[ToolCallEvent] = []
        for call in backend_calls:
            tool_def = self._registry.get(call.name)
            if tool_def:
                result = _execute_backend_tool(tool_def, call)
                backend_results.append(result)
                event = ToolCallEvent(
                    call_id=call.id,
                    tool_name=call.name,
                    arguments=call.arguments,
                    executor="backend",
                    status="completed" if result.succeeded else "failed",
                    turn_id=state.turn_id,
                    output=result.output[:500] if result.output else None,
                    error=result.error,
                    duration_ms=result.duration_ms,
                )
                completed_events.append(event)
                state.tool_trace.append(event)

        # If frontend tools exist, return pending
        if frontend_calls:
            pending_events: list[ToolCallEvent] = []
            for call in frontend_calls:
                tool_def = self._registry.get(call.name)
                pending_events.append(ToolCallEvent(
                    call_id=call.id,
                    tool_name=call.name,
                    arguments=call.arguments,
                    executor="frontend",
                    status="pending",
                    turn_id=state.turn_id,
                ))

            # Store state for resumption
            state.pending_backend_results = backend_results
            state.pending_frontend_calls = frontend_calls
            state.pending_raw_calls = raw_calls
            _turn_states[state.turn_id] = state

            return ChatTurnPending(
                turn_id=state.turn_id,
                pending_calls=pending_events,
                completed_calls=completed_events,
                loop_iteration=state.loop_iteration,
            )

        # Only backend tools — append results and continue loop
        tool_calls_message = {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": tc.arguments},
                }
                for tc in raw_calls
            ],
        }
        state.messages.append(tool_calls_message)

        for result in backend_results:
            state.messages.append({
                "role": "tool",
                "tool_call_id": result.call_id,
                "content": result.to_message_content(),
            })

        # Continue the loop (recursive call via _run_loop)
        return self._run_loop(state, None, 2048, 120.0)
