"""Standard tool definitions for the OperationController adapters.

Consumes the canonical tool contracts from tool_contracts.py.
These schemas are passed to ToolLoop.build_tool_schemas() so the LLM knows
which tools are available. The ToolLoopConfig's allowed_tools/prohibited_tools
filter this list at runtime.

Do NOT define tool schemas here — edit tool_contracts.py instead.
"""
from __future__ import annotations

from operation_controller.tool_contracts import production_tool_definitions

#: All canonical tool definitions — the single source of truth.
#: Policy (allowed/prohibited lists in adapters) determines which are active.
STANDARD_TOOL_DEFINITIONS: list[dict] = production_tool_definitions()
