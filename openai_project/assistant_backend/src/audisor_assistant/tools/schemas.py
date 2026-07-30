"""Tool-calling schemas shared across the registry, orchestrator, and API layer.

These are internal data structures — not Pydantic models (those live in
``schemas/chat.py`` for the HTTP boundary).  Provider-layer types
(``ToolSchema``, ``ToolCallRequest``) are separate and defined in
``providers/base.py``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal


class ToolExecutor(str, Enum):
    """Who physically executes a tool call."""

    FRONTEND = "frontend"
    BACKEND = "backend"


@dataclass(frozen=True)
class ToolDefinition:
    """Complete definition of a single tool available to the model.

    ``parameters`` is a JSON-Schema-compatible dict describing the
    function's input arguments (OpenAI tools format).
    """

    name: str
    description: str
    parameters: dict[str, Any]
    executor: ToolExecutor
    requires_approval: bool = False
    read_only: bool = True

    def to_provider_schema(self) -> dict[str, Any]:
        """Convert to the OpenAI-compatible tool schema sent to the model."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass(frozen=True)
class ToolCall:
    """A tool call as requested by the model (parsed from provider response)."""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ToolResult:
    """Result of executing a tool call."""

    call_id: str
    name: str
    output: str | None = None
    error: str | None = None
    status: Literal["success", "error", "timeout", "cancelled", "approved", "denied"] = "success"
    duration_ms: int | None = None
    operation_id: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.status == "success"

    def to_message_content(self) -> str:
        """Format as the content string for a tool-result message to the model."""
        if self.error:
            return f"Error: {self.error}"
        return self.output or ""
