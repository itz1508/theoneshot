"""Tool registry — central catalogue of all tools available to operator chat.

The registry owns tool definitions and provides filtering by executor
availability (e.g. exclude frontend tools when WebContainer is not booted).
"""
from __future__ import annotations

from typing import Sequence

from .schemas import ToolDefinition, ToolExecutor


class ToolRegistry:
    """Central tool catalogue.

    Tools are registered at import time (module-level in definitions/).
    The orchestrator queries the registry at request time to build the
    tool schemas sent to the model.
    """

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, tool: ToolDefinition) -> None:
        """Register a tool definition. Raises on duplicate name."""
        if tool.name in self._tools:
            raise ValueError(f"Tool already registered: {tool.name!r}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> ToolDefinition | None:
        """Look up a tool by name."""
        return self._tools.get(name)

    def all_tools(self) -> list[ToolDefinition]:
        """All registered tool definitions."""
        return list(self._tools.values())

    def list_schemas(
        self, *, workspace_available: bool = False
    ) -> list[dict]:
        """Return OpenAI-compatible tool schemas filtered by availability.

        When ``workspace_available`` is False, frontend-owned tools are
        excluded (the model won't request tools that can't execute).
        """
        tools = self.filter_by_availability(workspace_available)
        return [t.to_provider_schema() for t in tools]

    def filter_by_availability(
        self, workspace_available: bool
    ) -> list[ToolDefinition]:
        """Filter tools based on current executor availability."""
        result: list[ToolDefinition] = []
        for tool in self._tools.values():
            if tool.executor == ToolExecutor.FRONTEND and not workspace_available:
                continue
            result.append(tool)
        return result

    def executor_for(self, name: str) -> ToolExecutor | None:
        """Return the executor owner for a tool name, or None if unknown."""
        tool = self._tools.get(name)
        return tool.executor if tool else None

    @property
    def has_backend_tools(self) -> bool:
        """True if at least one backend-owned tool is registered."""
        return any(
            t.executor == ToolExecutor.BACKEND for t in self._tools.values()
        )

    @property
    def has_frontend_tools(self) -> bool:
        """True if at least one frontend-owned tool is registered."""
        return any(
            t.executor == ToolExecutor.FRONTEND for t in self._tools.values()
        )


# Module-level singleton — tool definitions register against this instance.
default_registry = ToolRegistry()
