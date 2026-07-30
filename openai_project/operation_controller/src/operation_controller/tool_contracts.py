"""Canonical tool contracts — single source of truth for tool identity and schema.

Every tool available to the model is defined here exactly once. Both the
OperationController path and the assistant-backend registry consume these
definitions. Provider-specific formatting (e.g. OpenAI tool schema wrapping)
is done by the consumer, not here.

Each contract includes:
- name: unique tool identifier
- description: human-readable purpose sent to the model
- parameters: JSON Schema for arguments (always additionalProperties: false)
- capability: read | execute | mutate | analyse
- execution_owner: frontend | backend
- requires_approval: whether operator must approve before execution
- read_only: whether the tool can modify state

Executor truth (verified 2026-07-27):
- file_read: WebContainer fs.readFile — frontend, read-only
- list_directory: WebContainer fs.readdir — frontend, read-only
- shell_exec: WebContainer spawn — frontend, mutating, approval required
- file_write: WebContainer fs.writeFile — frontend, mutating, approval required
- audisor_scan: backend executor — backend, read-only analysis
"""
from __future__ import annotations

from typing import Any


# ─── Capability classification ────────────────────────────────────────────────

CAPABILITY_READ = "read"
CAPABILITY_EXECUTE = "execute"
CAPABILITY_MUTATE = "mutate"
CAPABILITY_ANALYSE = "analyse"

# ─── Execution owner ─────────────────────────────────────────────────────────

OWNER_FRONTEND = "frontend"
OWNER_BACKEND = "backend"


# ─── Canonical definitions ────────────────────────────────────────────────────


def _file_read() -> dict[str, Any]:
    return {
        "name": "file_read",
        "description": (
            "Read the contents of a file at the given path. "
            "Returns the file text. Use this to inspect source code, "
            "configuration files, test files, or any text file in the project."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": (
                        "Relative path to the file within the workspace root. "
                        "Use forward slashes (e.g. 'src/index.ts', 'package.json')."
                    ),
                },
            },
            "required": ["path"],
            "additionalProperties": False,
        },
        "capability": CAPABILITY_READ,
        "execution_owner": OWNER_FRONTEND,
        "requires_approval": False,
        "read_only": True,
    }


def _list_directory() -> dict[str, Any]:
    return {
        "name": "list_directory",
        "description": (
            "List the files and directories at a given path in the active project "
            "workspace. Returns entries with their type (file or directory). "
            "Use this to explore project structure before reading specific files."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": (
                        "Relative path to the directory within the workspace root. "
                        "Use '.' or '' for the root. Use forward slashes."
                    ),
                    "default": ".",
                },
            },
            "required": [],
            "additionalProperties": False,
        },
        "capability": CAPABILITY_READ,
        "execution_owner": OWNER_FRONTEND,
        "requires_approval": False,
        "read_only": True,
    }


def _shell_exec() -> dict[str, Any]:
    return {
        "name": "shell_exec",
        "description": (
            "Execute a shell command in the active project workspace. "
            "The command runs inside the WebContainer's Node.js environment. "
            "Use this for: running tests (npm test), linting (npm run lint), "
            "building (npm run build), installing packages (npm install), "
            "or any other shell command. Returns the command's stdout/stderr "
            "output and exit code."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": (
                        "The command to execute (e.g. 'npm test', 'npm run build', "
                        "'cat src/main.ts', 'ls -la'). Runs via the shell."
                    ),
                },
            },
            "required": ["command"],
            "additionalProperties": False,
        },
        "capability": CAPABILITY_EXECUTE,
        "execution_owner": OWNER_FRONTEND,
        "requires_approval": True,
        "read_only": False,
    }


def _file_write() -> dict[str, Any]:
    """Canonical contract for file_write — defined for future use.

    This tool is NOT enabled in the production operation path during
    the current milestone. The model must not call it; if it does,
    the capability policy rejects it.
    """
    return {
        "name": "file_write",
        "description": (
            "Write content to a file at the given path. "
            "Creates the file if it doesn't exist, overwrites if it does."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative path to the file to write.",
                },
                "content": {
                    "type": "string",
                    "description": "The content to write to the file.",
                },
            },
            "required": ["path", "content"],
            "additionalProperties": False,
        },
        "capability": CAPABILITY_MUTATE,
        "execution_owner": OWNER_FRONTEND,
        "requires_approval": True,
        "read_only": False,
    }


def _audisor_scan() -> dict[str, Any]:
    return {
        "name": "audisor_scan",
        "description": (
            "Run an Audisor scan on a file or directory. Analyzes code for "
            "structural issues, dead code, import problems, and provides "
            "a summary of findings. This is a read-only analysis operation "
            "that does not modify any files."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": (
                        "Path to scan, relative to the repository root. "
                        "Can be a file or directory."
                    ),
                },
                "depth": {
                    "type": "string",
                    "enum": ["surface", "standard", "deep"],
                    "description": (
                        "Scan depth: 'surface' for quick overview, 'standard' for "
                        "normal analysis, 'deep' for thorough inspection."
                    ),
                    "default": "standard",
                },
            },
            "required": ["target"],
            "additionalProperties": False,
        },
        "capability": CAPABILITY_ANALYSE,
        "execution_owner": OWNER_BACKEND,
        "requires_approval": False,
        "read_only": True,
    }


# ─── Registry ─────────────────────────────────────────────────────────────────

#: All canonical tool contracts keyed by name.
_CANONICAL_TOOLS: dict[str, dict[str, Any]] = {
    "file_read": _file_read(),
    "list_directory": _list_directory(),
    "shell_exec": _shell_exec(),
    "file_write": _file_write(),
    "audisor_scan": _audisor_scan(),
}


def get_canonical_tool(name: str) -> dict[str, Any]:
    """Return the canonical contract for a tool by name.

    Raises KeyError if the tool is not defined.
    """
    return _CANONICAL_TOOLS[name]


def all_canonical_tools() -> dict[str, dict[str, Any]]:
    """Return all canonical tool contracts (name → definition)."""
    return dict(_CANONICAL_TOOLS)


def canonical_tool_names() -> frozenset[str]:
    """Return the set of all canonical tool names."""
    return frozenset(_CANONICAL_TOOLS.keys())


def production_tool_definitions() -> list[dict[str, Any]]:
    """Return canonical definitions for the production tool set.

    This is the list consumed by OperationController adapters and the
    assistant-backend registry. It includes all canonical tools;
    policy (allowed/prohibited lists) determines which are active.
    """
    return [dict(defn) for defn in _CANONICAL_TOOLS.values()]


def to_openai_tool_schema(tool_def: dict[str, Any]) -> dict[str, Any]:
    """Convert a canonical tool definition to OpenAI-compatible schema.

    This is the format sent to the model provider. It strips runtime
    metadata (capability, execution_owner, etc.) and keeps only the
    function signature.
    """
    return {
        "type": "function",
        "function": {
            "name": tool_def["name"],
            "description": tool_def["description"],
            "parameters": tool_def["parameters"],
        },
    }


def to_openai_tool_schemas(
    tool_names: frozenset[str] | None = None,
) -> list[dict[str, Any]]:
    """Build OpenAI tool schemas for a set of tool names.

    If tool_names is None, all canonical tools are included.
    """
    names = tool_names if tool_names is not None else canonical_tool_names()
    return [
        to_openai_tool_schema(_CANONICAL_TOOLS[name])
        for name in sorted(names)
        if name in _CANONICAL_TOOLS
    ]


def requires_approval(name: str) -> bool:
    """Check whether a canonical tool requires operator approval."""
    tool = _CANONICAL_TOOLS.get(name)
    if tool is None:
        return False
    return tool.get("requires_approval", False)


def execution_owner(name: str) -> str | None:
    """Return the execution owner for a canonical tool, or None."""
    tool = _CANONICAL_TOOLS.get(name)
    if tool is None:
        return None
    return tool.get("execution_owner")


def capability(name: str) -> str | None:
    """Return the capability classification for a canonical tool, or None."""
    tool = _CANONICAL_TOOLS.get(name)
    if tool is None:
        return None
    return tool.get("capability")
