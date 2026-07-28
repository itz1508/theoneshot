"""Domain layer: modes and per-mode result contracts."""

from .modes import AssistantMode, mode_requires_selected_text
from .results import MODE_RESULT_MODELS, SelectionRequiredResult

__all__ = [
    "AssistantMode",
    "mode_requires_selected_text",
    "MODE_RESULT_MODELS",
    "SelectionRequiredResult",
]
