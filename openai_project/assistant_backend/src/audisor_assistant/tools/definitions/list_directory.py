"""list_directory — list contents of a directory in the workspace.

Executor: frontend (WebContainer)
Read-only: True
Requires approval: False

Schema sourced from canonical tool contracts — do not edit locally.
"""
from ..schemas import ToolDefinition, ToolExecutor
from ..registry import default_registry

try:
    from operation_controller.tool_contracts import get_canonical_tool
    _canonical = get_canonical_tool("list_directory")
except ImportError:
    _canonical = None

if _canonical is not None:
    LIST_DIRECTORY_TOOL = ToolDefinition(
        name=_canonical["name"],
        description=_canonical["description"],
        parameters=_canonical["parameters"],
        executor=ToolExecutor.FRONTEND,
        requires_approval=_canonical.get("requires_approval", False),
        read_only=_canonical.get("read_only", True),
    )
else:
    LIST_DIRECTORY_TOOL = ToolDefinition(
        name="list_directory",
        description=(
            "List the files and directories at a given path in the active project "
            "workspace. Returns entries with their type (file or directory). "
            "Use this to explore project structure before reading specific files."
        ),
        parameters={
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
        executor=ToolExecutor.FRONTEND,
        requires_approval=False,
        read_only=True,
    )

default_registry.register(LIST_DIRECTORY_TOOL)
