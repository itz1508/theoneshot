"""shell_exec — run a shell command in the WebContainer workspace.

Executor: frontend (WebContainer)
Read-only: False (commands may modify files)
Requires approval: True

Schema sourced from canonical tool contracts — do not edit locally.
"""
from ..schemas import ToolDefinition, ToolExecutor
from ..registry import default_registry

try:
    from operation_controller.tool_contracts import get_canonical_tool
    _canonical = get_canonical_tool("shell_exec")
except ImportError:
    _canonical = None

if _canonical is not None:
    SHELL_EXEC_TOOL = ToolDefinition(
        name=_canonical["name"],
        description=_canonical["description"],
        parameters=_canonical["parameters"],
        executor=ToolExecutor.FRONTEND,
        requires_approval=_canonical.get("requires_approval", True),
        read_only=_canonical.get("read_only", False),
    )
else:
    SHELL_EXEC_TOOL = ToolDefinition(
        name="shell_exec",
        description=(
            "Execute a shell command in the active project workspace. "
            "The command runs inside the WebContainer's Node.js environment. "
            "Use this for: running tests (npm test), linting (npm run lint), "
            "building (npm run build), installing packages (npm install), "
            "or any other shell command. Returns the command's stdout/stderr "
            "output and exit code."
        ),
        parameters={
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
        executor=ToolExecutor.FRONTEND,
        requires_approval=True,
        read_only=False,
    )

default_registry.register(SHELL_EXEC_TOOL)
