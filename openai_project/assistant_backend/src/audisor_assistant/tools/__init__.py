"""Operator-chat tool registry and definitions.

Provides:
- ToolRegistry: central registry of all available tools
- Tool schemas and result types
- Individual tool definitions for file, shell, and Audisor operations
"""
from .registry import ToolRegistry, default_registry
from .schemas import (
    ToolDefinition,
    ToolCall,
    ToolResult,
    ToolExecutor,
)

__all__ = [
    "ToolRegistry",
    "default_registry",
    "ToolDefinition",
    "ToolCall",
    "ToolResult",
    "ToolExecutor",
]
