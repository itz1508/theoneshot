"""audisor_scan — run an Audisor scan on a target path.

Executor: backend
Read-only: True
Requires approval: False

Schema sourced from canonical tool contracts — do not edit locally.
"""
from ..schemas import ToolDefinition, ToolExecutor
from ..registry import default_registry

try:
    from operation_controller.tool_contracts import get_canonical_tool
    _canonical = get_canonical_tool("audisor_scan")
except ImportError:
    _canonical = None

if _canonical is not None:
    AUDISOR_SCAN_TOOL = ToolDefinition(
        name=_canonical["name"],
        description=_canonical["description"],
        parameters=_canonical["parameters"],
        executor=ToolExecutor.BACKEND,
        requires_approval=_canonical.get("requires_approval", False),
        read_only=_canonical.get("read_only", True),
    )
else:
    AUDISOR_SCAN_TOOL = ToolDefinition(
        name="audisor_scan",
        description=(
            "Run an Audisor scan on a file or directory. Analyzes code for "
            "structural issues, dead code, import problems, and provides "
            "a summary of findings. This is a read-only analysis operation "
            "that does not modify any files."
        ),
        parameters={
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
        executor=ToolExecutor.BACKEND,
        requires_approval=False,
        read_only=True,
    )

default_registry.register(AUDISOR_SCAN_TOOL)
