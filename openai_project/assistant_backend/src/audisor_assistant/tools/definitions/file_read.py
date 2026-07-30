"""file_read — read a file from the active WebContainer workspace.

Executor: frontend (WebContainer)
Read-only: True
Requires approval: False

Schema sourced from canonical tool contracts — do not edit locally.
"""
from ..schemas import ToolDefinition, ToolExecutor
from ..registry import default_registry

try:
    from operation_controller.tool_contracts import get_canonical_tool
    _canonical = get_canonical_tool("file_read")
except ImportError:
    _canonical = None

if _canonical is not None:
    FILE_READ_TOOL = ToolDefinition(
        name=_canonical["name"],
        description=_canonical["description"],
        parameters=_canonical["parameters"],
        executor=ToolExecutor.FRONTEND,
        requires_approval=_canonical.get("requires_approval", False),
        read_only=_canonical.get("read_only", True),
    )
else:
    # Fallback if operation_controller is not importable
    FILE_READ_TOOL = ToolDefinition(
        name="file_read",
        description=(
            "Read the contents of a file from the active project workspace. "
            "Returns the file content as text. Use this to inspect source code, "
            "configuration files, test files, or any text file in the project."
        ),
        parameters={
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
        executor=ToolExecutor.FRONTEND,
        requires_approval=False,
        read_only=True,
    )

default_registry.register(FILE_READ_TOOL)
