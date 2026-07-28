"""Assistant modes.

The six supported modes are a closed set.  Mode identity is the contract
between the web client, the request schema, and the per-mode result models.
"""
from __future__ import annotations

from enum import Enum


class AssistantMode(str, Enum):
    FIX_WORDING = "fix_wording"
    DRAFT_THREE_REPLIES = "draft_three_replies"
    TRANSLATE_SLANG_JARGON = "translate_slang_jargon"
    TEACH_CLEARLY = "teach_clearly"
    EXPAND_IDEA = "expand_idea"
    VISUALIZE_DESIGN = "visualize_design"


#: Modes that require a non-empty ``selected_text`` before a provider call.
MODES_REQUIRING_SELECTED_TEXT = frozenset({AssistantMode.TRANSLATE_SLANG_JARGON})


def mode_requires_selected_text(mode: AssistantMode) -> bool:
    return mode in MODES_REQUIRING_SELECTED_TEXT
