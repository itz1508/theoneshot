"""Per-mode result contracts.

Provider output is untrusted text.  Each mode's JSON payload is validated
against the matching model here before it enters the response envelope.
Unknown provider fields are rejected so raw provider payloads can never
leak through the public API.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .modes import AssistantMode


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FixWordingChange(_StrictModel):
    original: str
    correction: str
    reason: str
    intentional_possible: bool = False
    # Grammar-checker detail (absent/defaulted on model-produced changes).
    offset: int | None = None
    length: int | None = None
    rule_id: str = ""
    replacements: list[str] = Field(default_factory=list)


class FixWordingResult(_StrictModel):
    corrected_text: str
    # Semantic shape only; execution provenance (engine/fallback) lives in
    # the response envelope, never here.
    result_kind: Literal["model", "languagetool"] = "model"
    changes: list[FixWordingChange] = Field(default_factory=list)
    no_changes_needed: bool = False
    inferred_intent: str = ""
    tone: str = ""
    context: str = ""
    assumptions: list[str] = Field(default_factory=list)
    uncertainty: list[str] = Field(default_factory=list)


class DraftThreeRepliesResult(_StrictModel):
    in_short: str
    brief: str
    thorough: str
    diplomatic: str
    message_purpose: str
    tone: str
    uncertainty: list[str] = Field(default_factory=list)


class TranslationExample(_StrictModel):
    original: str
    professional: str


class TranslateSlangJargonResult(_StrictModel):
    term: str
    professional_translation: str
    plain_meaning: str
    origin_context: str
    usage_notes: list[str] = Field(default_factory=list)
    example: TranslationExample


class SelectionRequiredResult(_StrictModel):
    """Structured result returned when translate mode has no selected term."""

    selection_required: bool = True
    message: str = "Select the slang or jargon term to translate."


class TeachClearlyResult(_StrictModel):
    basics: str
    building_from_there: list[str] = Field(default_factory=list)
    key_insights: list[str] = Field(default_factory=list)
    common_misconceptions: list[str] = Field(default_factory=list)
    why_this_matters: str
    check_your_understanding: list[str] = Field(default_factory=list)


class ExpandIdeaResult(_StrictModel):
    expanded_text: str
    preserved_intent: str
    added_assumptions: list[str] = Field(default_factory=list)
    uncertainty: list[str] = Field(default_factory=list)


class VisualizeDesignResult(_StrictModel):
    """Kind-based diagram contract.

    ``layout`` carries collapsed/expanded ASCII trees, ``workflow``
    carries Mermaid diagram code, ``unclear`` carries only the summary
    explaining what is missing.
    """

    kind: Literal["layout", "workflow", "unclear"]
    summary: str
    collapsed: list[str] | None = None
    expanded: list[str] | None = None
    diagram_code: str | None = None
    builder_prompt: str | None = None
    warnings: list[str] = Field(default_factory=list)


MODE_RESULT_MODELS: dict[AssistantMode, type[_StrictModel]] = {
    AssistantMode.FIX_WORDING: FixWordingResult,
    AssistantMode.DRAFT_THREE_REPLIES: DraftThreeRepliesResult,
    AssistantMode.TRANSLATE_SLANG_JARGON: TranslateSlangJargonResult,
    AssistantMode.TEACH_CLEARLY: TeachClearlyResult,
    AssistantMode.EXPAND_IDEA: ExpandIdeaResult,
    AssistantMode.VISUALIZE_DESIGN: VisualizeDesignResult,
}
