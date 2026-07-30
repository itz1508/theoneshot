"""Shared ToolLoop utility — lifecycle-neutral LLM tool-calling loop.

The ToolLoop:
- Calls the provider with tool schemas.
- Parses structured tool calls from the response.
- Executes backend-owned tools locally.
- Returns suspension outcomes for frontend-owned or approval-required tools.
- Enforces loop limits, timeouts, output budgets, and cancellation.

It does NOT transition operation states or persist operation records.
Adapters invoke it and interpret the outcome.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from threading import Event
from typing import Any, Protocol

logger = logging.getLogger("operation_controller.tool_loop")


# ─── Configuration ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ToolLoopConfig:
    """Policy governing a single ToolLoop execution."""

    allowed_tools: frozenset[str]
    prohibited_tools: frozenset[str] = frozenset()
    mutation_allowed: bool = False
    approval_required_tools: frozenset[str] = frozenset()
    max_iterations: int = 15
    timeout_seconds: float = 300.0
    max_output_bytes: int = 100_000
    max_aggregate_bytes: int = 500_000
    provider_model: str | None = None
    system_prompt: str = ""

    def is_tool_allowed(self, name: str) -> bool:
        if name in self.prohibited_tools:
            return False
        return name in self.allowed_tools

    def requires_approval(self, name: str) -> bool:
        return name in self.approval_required_tools


# ─── Data types ───────────────────────────────────────────────────────────────


@dataclass
class UsageRecord:
    """Token usage from a single provider call."""

    provider: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost: float | None = None
    timestamp: str = ""


@dataclass
class ToolTraceEntry:
    """Record of a single tool invocation."""

    call_id: str
    tool_name: str
    arguments: dict[str, Any]
    executor: str  # "backend" | "frontend"
    status: str  # "completed" | "failed" | "pending" | "approval_required"
    output: str | None = None
    error: str | None = None
    duration_ms: int | None = None


@dataclass
class PendingToolCall:
    """A tool call awaiting frontend execution or approval."""

    call_id: str
    tool_name: str
    arguments: dict[str, Any]
    raw_arguments: str = ""  # original JSON from model


# ─── LoopContinuation (serializable) ─────────────────────────────────────────


@dataclass
class LoopContinuation:
    """Serializable state needed to resume a suspended loop.

    Persisted by the adapter into OperationRecord.artifacts.
    """

    messages: list[dict[str, Any]]
    pending_raw_calls: list[dict[str, Any]]
    loop_iteration: int
    usage_totals: dict[str, int]
    tool_trace: list[dict[str, Any]]
    config_snapshot: dict[str, Any]
    version: int = 1

    def serialize(self) -> str:
        """Serialize to JSON for persistence."""
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def deserialize(cls, data: str) -> "LoopContinuation":
        """Deserialize from JSON."""
        raw = json.loads(data)
        return cls(**raw)


# ─── Outcome types (discriminated union) ──────────────────────────────────────


@dataclass
class ToolLoopCompleted:
    """Loop finished with a final text response."""

    reply: str
    usage: list[UsageRecord]
    tool_trace: list[ToolTraceEntry]
    finish_reason: str = "stop"


@dataclass
class ToolLoopFailed:
    """Loop failed (timeout, cancellation, or provider error)."""

    error: str
    usage: list[UsageRecord]
    tool_trace: list[ToolTraceEntry]


@dataclass
class ToolLoopSuspendedForTool:
    """Loop suspended waiting for frontend tool execution."""

    pending_calls: list[PendingToolCall]
    continuation: LoopContinuation
    usage: list[UsageRecord]
    tool_trace: list[ToolTraceEntry]
    backend_results: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ToolLoopSuspendedForApproval:
    """Loop suspended waiting for operator approval."""

    tool_call: PendingToolCall
    reason: str
    risk_level: str
    continuation: LoopContinuation
    usage: list[UsageRecord]
    tool_trace: list[ToolTraceEntry]


ToolLoopOutcome = (
    ToolLoopCompleted
    | ToolLoopFailed
    | ToolLoopSuspendedForTool
    | ToolLoopSuspendedForApproval
)


# ─── Provider protocol (minimal interface needed by the loop) ─────────────────


class ToolLoopProvider(Protocol):
    """Minimal provider interface for the tool loop.

    Adapters wrap the full AssistantProvider to satisfy this protocol.
    """

    def call(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        model: str | None,
        max_tokens: int,
        timeout_seconds: float,
    ) -> "ProviderResponse":
        """Call the LLM with messages and tool schemas."""
        ...


@dataclass
class ProviderResponse:
    """Normalized response from a provider call."""

    text: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)
    model: str = ""
    finish_reason: str = "stop"


# ─── Backend tool executor protocol ──────────────────────────────────────────


class BackendToolExecutor(Protocol):
    """Executes a backend-owned tool and returns the result."""

    def execute(self, tool_name: str, arguments: dict[str, Any]) -> "ToolExecutionResult":
        ...


@dataclass
class ToolExecutionResult:
    """Result of executing a backend tool."""

    output: str = ""
    error: str | None = None
    duration_ms: int = 0

    @property
    def succeeded(self) -> bool:
        return self.error is None


# ─── ToolLoop ─────────────────────────────────────────────────────────────────


class ToolLoop:
    """Shared, lifecycle-neutral LLM tool-calling loop.

    Adapters construct with config + provider + executor, then call run().
    The loop returns a structured outcome; the adapter interprets it.
    """

    def __init__(
        self,
        config: ToolLoopConfig,
        provider: ToolLoopProvider,
        backend_executor: BackendToolExecutor | None = None,
    ) -> None:
        self._config = config
        self._provider = provider
        self._executor = backend_executor

    def build_tool_schemas(self, tool_definitions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Filter tool definitions to those allowed by config."""
        schemas = []
        for tool_def in tool_definitions:
            name = tool_def.get("name", "")
            if self._config.is_tool_allowed(name):
                schemas.append({
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": tool_def.get("description", ""),
                        "parameters": tool_def.get("parameters", {}),
                    },
                })
        return schemas

    def run(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        abort: Event | None = None,
        max_tokens: int = 2048,
    ) -> ToolLoopOutcome:
        """Execute the tool-calling loop from the beginning.

        Args:
            messages: Initial message buffer (system + history + user).
            tools: OpenAI-format tool schemas (already filtered).
            abort: Threading event; if set, loop exits with cancellation.
            max_tokens: Max tokens per provider call.

        Returns:
            One of the ToolLoopOutcome variants.
        """
        state = _LoopState(
            messages=list(messages),
            tools=tools,
            config=self._config,
        )
        return self._execute(state, abort, max_tokens)

    def resume(
        self,
        continuation: LoopContinuation,
        tool_results: list[dict[str, Any]],
        *,
        abort: Event | None = None,
        max_tokens: int = 2048,
    ) -> ToolLoopOutcome:
        """Resume a suspended loop with tool results.

        Args:
            continuation: Persisted loop state from suspension.
            tool_results: Results for pending tool calls.
                Each dict has: call_id, tool_name, output, error, status.
            abort: Threading event for cancellation.
            max_tokens: Max tokens per provider call.
        """
        # Rebuild state from continuation
        state = _LoopState(
            messages=list(continuation.messages),
            tools=[],  # Will be reconstructed from config snapshot
            config=self._config,
            loop_iteration=continuation.loop_iteration,
            usage_totals=dict(continuation.usage_totals),
            tool_trace=[ToolTraceEntry(**t) for t in continuation.tool_trace],
        )

        # Append the assistant's tool_calls message
        if continuation.pending_raw_calls:
            tool_calls_msg = {
                "role": "assistant",
                "content": None,
                "tool_calls": continuation.pending_raw_calls,
            }
            state.messages.append(tool_calls_msg)

        # Append tool result messages
        for result in tool_results:
            content = result.get("output") or result.get("error") or ""
            state.messages.append({
                "role": "tool",
                "tool_call_id": result["call_id"],
                "content": content,
            })
            # Record in trace
            state.tool_trace.append(ToolTraceEntry(
                call_id=result["call_id"],
                tool_name=result.get("tool_name", ""),
                arguments={},
                executor="frontend",
                status="completed" if result.get("status") == "success" else "failed",
                output=(result.get("output") or "")[:500],
                error=result.get("error"),
            ))

        # Rebuild tools from config snapshot
        # The adapter should pass tools again or we use an empty list
        # (the provider will use whatever tools are in the schema)
        return self._execute(state, abort, max_tokens)

    def _execute(
        self,
        state: "_LoopState",
        abort: Event | None,
        max_tokens: int,
    ) -> ToolLoopOutcome:
        """Internal loop execution."""
        loop_start = time.time()
        usage_records: list[UsageRecord] = []

        while state.loop_iteration < self._config.max_iterations:
            # Check cancellation
            if abort and abort.is_set():
                return ToolLoopFailed(
                    error="cancelled",
                    usage=usage_records,
                    tool_trace=state.tool_trace,
                )

            # Check timeout
            elapsed = time.time() - loop_start
            if elapsed > self._config.timeout_seconds:
                return ToolLoopFailed(
                    error="timeout",
                    usage=usage_records,
                    tool_trace=state.tool_trace,
                )

            state.loop_iteration += 1

            # Call provider
            try:
                response = self._provider.call(
                    messages=state.messages,
                    tools=state.tools,
                    model=self._config.provider_model,
                    max_tokens=max_tokens,
                    timeout_seconds=self._config.timeout_seconds - elapsed,
                )
            except Exception as exc:
                return ToolLoopFailed(
                    error=f"provider_error: {exc}",
                    usage=usage_records,
                    tool_trace=state.tool_trace,
                )

            # Record usage
            if response.usage:
                usage_records.append(UsageRecord(
                    provider="openai_compatible",
                    model=response.model or self._config.provider_model or "",
                    prompt_tokens=response.usage.get("prompt_tokens", 0),
                    completion_tokens=response.usage.get("completion_tokens", 0),
                    total_tokens=response.usage.get("total_tokens", 0),
                    timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                ))
                for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                    state.usage_totals[key] = state.usage_totals.get(key, 0) + response.usage.get(key, 0)

            # No tool calls — final response
            if not response.tool_calls:
                return ToolLoopCompleted(
                    reply=response.text,
                    usage=usage_records,
                    tool_trace=state.tool_trace,
                    finish_reason=response.finish_reason,
                )

            # Process tool calls
            outcome = self._handle_tool_calls(state, response, usage_records)
            if outcome is not None:
                return outcome

            # If _handle_tool_calls returned None, all tools were backend-executed
            # and appended to messages — continue the loop.

        # Loop limit reached
        return ToolLoopFailed(
            error="loop_limit_exceeded",
            usage=usage_records,
            tool_trace=state.tool_trace,
        )

    def _handle_tool_calls(
        self,
        state: "_LoopState",
        response: ProviderResponse,
        usage_records: list[UsageRecord],
    ) -> ToolLoopOutcome | None:
        """Process tool calls from the model response.

        Returns an outcome if suspension is needed, or None if all tools
        were executed locally and the loop should continue.
        """
        backend_calls: list[tuple[dict[str, Any], dict[str, Any]]] = []
        frontend_calls: list[PendingToolCall] = []
        approval_calls: list[PendingToolCall] = []
        aggregate_output = 0

        for tc in response.tool_calls:
            call_id = tc.get("id", f"call-{uuid.uuid4().hex[:8]}")
            name = tc.get("function", {}).get("name", tc.get("name", ""))
            raw_args = tc.get("function", {}).get("arguments", tc.get("arguments", "{}"))

            try:
                arguments = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
            except (json.JSONDecodeError, TypeError):
                arguments = {}

            # Check if tool is allowed
            if not self._config.is_tool_allowed(name):
                state.tool_trace.append(ToolTraceEntry(
                    call_id=call_id,
                    tool_name=name,
                    arguments=arguments,
                    executor="blocked",
                    status="failed",
                    error=f"Tool '{name}' not allowed by policy",
                ))
                continue

            # Check approval requirement
            if self._config.requires_approval(name):
                approval_calls.append(PendingToolCall(
                    call_id=call_id,
                    tool_name=name,
                    arguments=arguments,
                    raw_arguments=raw_args if isinstance(raw_args, str) else json.dumps(raw_args),
                ))
                continue

            # Determine executor: backend has an executor, frontend does not
            if self._executor and self._can_execute_backend(name):
                backend_calls.append((
                    {"id": call_id, "name": name, "arguments": raw_args},
                    arguments,
                ))
            else:
                frontend_calls.append(PendingToolCall(
                    call_id=call_id,
                    tool_name=name,
                    arguments=arguments,
                    raw_arguments=raw_args if isinstance(raw_args, str) else json.dumps(raw_args),
                ))

        # If approval is needed, suspend immediately
        if approval_calls:
            call = approval_calls[0]
            continuation = self._build_continuation(state, response)
            return ToolLoopSuspendedForApproval(
                tool_call=call,
                reason=f"Tool '{call.tool_name}' requires operator approval.",
                risk_level="medium" if self._config.mutation_allowed else "low",
                continuation=continuation,
                usage=usage_records,
                tool_trace=state.tool_trace,
            )

        # If frontend calls exist, suspend for tool execution
        if frontend_calls:
            # Execute backend calls first
            backend_results = self._execute_backend_batch(backend_calls, state)
            continuation = self._build_continuation(state, response)
            return ToolLoopSuspendedForTool(
                pending_calls=frontend_calls,
                continuation=continuation,
                usage=usage_records,
                tool_trace=state.tool_trace,
                backend_results=backend_results,
            )

        # All backend — execute and continue
        if backend_calls:
            self._execute_backend_batch_inline(backend_calls, state, response)
            return None

        # No actionable calls (all blocked)
        # Append an empty assistant message to continue
        state.messages.append({
            "role": "assistant",
            "content": "All requested tools were blocked by policy.",
        })
        return None

    def _can_execute_backend(self, name: str) -> bool:
        """Check if a tool can be executed on the backend."""
        # Backend tools are those with an executor registered
        # Frontend tools are file_read, file_write, list_directory, shell_exec
        frontend_tools = {"file_read", "file_write", "list_directory", "shell_exec"}
        return name not in frontend_tools

    def _execute_backend_batch(
        self,
        calls: list[tuple[dict[str, Any], dict[str, Any]]],
        state: "_LoopState",
    ) -> list[dict[str, Any]]:
        """Execute backend tools and return results (for combining with frontend)."""
        results = []
        for raw_call, arguments in calls:
            name = raw_call["name"]
            call_id = raw_call["id"]
            start = time.time()

            if self._executor:
                result = self._executor.execute(name, arguments)
                duration = int((time.time() - start) * 1000)
                output = result.output[:self._config.max_output_bytes]
                state.tool_trace.append(ToolTraceEntry(
                    call_id=call_id,
                    tool_name=name,
                    arguments=arguments,
                    executor="backend",
                    status="completed" if result.succeeded else "failed",
                    output=output[:500],
                    error=result.error,
                    duration_ms=duration,
                ))
                results.append({
                    "call_id": call_id,
                    "tool_name": name,
                    "output": output,
                    "error": result.error,
                    "status": "success" if result.succeeded else "error",
                })
            else:
                results.append({
                    "call_id": call_id,
                    "tool_name": name,
                    "output": None,
                    "error": "No backend executor configured",
                    "status": "error",
                })
        return results

    def _execute_backend_batch_inline(
        self,
        calls: list[tuple[dict[str, Any], dict[str, Any]]],
        state: "_LoopState",
        response: ProviderResponse,
    ) -> None:
        """Execute backend tools and append results to messages for loop continuation."""
        # First, append the assistant's tool_calls message
        state.messages.append({
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": raw["id"],
                    "type": "function",
                    "function": {"name": raw["name"], "arguments": raw["arguments"]},
                }
                for raw, _ in calls
            ],
        })

        # Execute each and append tool result messages
        for raw_call, arguments in calls:
            name = raw_call["name"]
            call_id = raw_call["id"]
            start = time.time()

            if self._executor:
                result = self._executor.execute(name, arguments)
                duration = int((time.time() - start) * 1000)
                output = result.output[:self._config.max_output_bytes]
                content = output if result.succeeded else (result.error or "execution failed")
            else:
                content = f"Tool '{name}' has no backend executor."
                output = ""
                duration = 0
                result = ToolExecutionResult(error=content)

            state.tool_trace.append(ToolTraceEntry(
                call_id=call_id,
                tool_name=name,
                arguments=arguments,
                executor="backend",
                status="completed" if result.succeeded else "failed",
                output=output[:500] if output else None,
                error=result.error,
                duration_ms=duration,
            ))

            state.messages.append({
                "role": "tool",
                "tool_call_id": call_id,
                "content": content,
            })

    def _build_continuation(
        self,
        state: "_LoopState",
        response: ProviderResponse,
    ) -> LoopContinuation:
        """Build a serializable continuation from current state."""
        # Convert raw tool_calls from provider response
        raw_calls = []
        for tc in response.tool_calls:
            raw_calls.append({
                "id": tc.get("id", ""),
                "type": "function",
                "function": {
                    "name": tc.get("function", {}).get("name", tc.get("name", "")),
                    "arguments": tc.get("function", {}).get("arguments", tc.get("arguments", "{}")),
                },
            })

        return LoopContinuation(
            messages=state.messages,
            pending_raw_calls=raw_calls,
            loop_iteration=state.loop_iteration,
            usage_totals=state.usage_totals,
            tool_trace=[asdict(t) for t in state.tool_trace],
            config_snapshot={
                "allowed_tools": sorted(self._config.allowed_tools),
                "prohibited_tools": sorted(self._config.prohibited_tools),
                "mutation_allowed": self._config.mutation_allowed,
                "max_iterations": self._config.max_iterations,
                "timeout_seconds": self._config.timeout_seconds,
                "max_output_bytes": self._config.max_output_bytes,
                "max_aggregate_bytes": self._config.max_aggregate_bytes,
                "provider_model": self._config.provider_model,
                "system_prompt": self._config.system_prompt,
            },
        )


# ─── Internal state ──────────────────────────────────────────────────────────


@dataclass
class _LoopState:
    """Mutable internal state for a running loop iteration."""

    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]]
    config: ToolLoopConfig
    loop_iteration: int = 0
    usage_totals: dict[str, int] = field(default_factory=dict)
    tool_trace: list[ToolTraceEntry] = field(default_factory=list)
