"""Strict input and output contracts for Audisor synonym generation."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


Tone = Literal["casual", "conversational", "neutral", "formal", "academic"]
TONES = ("casual", "conversational", "neutral", "formal", "academic")


def _non_empty(value: str, name: str) -> str:
    if not value.strip():
        raise ValueError(f"{name} must not be empty")
    return value.strip()


class SynonymRequest(BaseModel):
    """One selection and only the context explicitly supplied by the user."""

    model_config = ConfigDict(extra="forbid", strict=True)

    selected_text: str = ""
    surrounding_text: str | None = None
    selection_count: int | None = Field(default=None, ge=0, le=10_000)
    operation_id: str | None = Field(default=None, min_length=1, max_length=128)

    @field_validator("selected_text")
    @classmethod
    def normalize_selection(cls, value: str) -> str:
        return value.strip()

    @field_validator("surrounding_text")
    @classmethod
    def normalize_context(cls, value: str | None) -> str | None:
        return value.strip() if value is not None and value.strip() else None

    @model_validator(mode="after")
    def validate_selection_count(self) -> "SynonymRequest":
        derived = 0 if not self.selected_text else len(self.selected_text.split())
        if self.selection_count is not None and self.selection_count != derived:
            raise ValueError("selection_count does not match selected_text")
        return self

    @property
    def is_empty(self) -> bool:
        return not self.selected_text

    @property
    def is_phrase(self) -> bool:
        return bool(self.selected_text) and len(self.selected_text.split()) > 1


class SynonymAlternative(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    word_or_phrase: str = Field(min_length=1, max_length=256)
    tone: Tone
    usage_guidance: str = Field(min_length=1, max_length=2000)
    meaning_difference: str = Field(min_length=1, max_length=2000)
    connotation_warning: str | None = Field(default=None, max_length=2000)

    @field_validator("word_or_phrase", "usage_guidance", "meaning_difference")
    @classmethod
    def validate_text(cls, value: str) -> str:
        return _non_empty(value, "alternative field")


class SynonymRecommendation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    selected_alternative: str = Field(min_length=1, max_length=256)
    reason: str = Field(min_length=1, max_length=2000)

    @field_validator("selected_alternative", "reason")
    @classmethod
    def validate_text(cls, value: str) -> str:
        return _non_empty(value, "recommendation field")


class SynonymResponse(BaseModel):
    """Public response for both valid results and no-selection requests."""

    model_config = ConfigDict(extra="forbid", strict=True)

    status: Literal["ready", "needs_selection"]
    selected_text: str
    context: str
    interpreted_meaning: str | None = None
    part_of_speech: str | None = None
    alternatives: list[SynonymAlternative] = Field(default_factory=list)
    recommendation: SynonymRecommendation | None = None
    limitation: str | None = None
    follow_up_questions: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_result(self) -> "SynonymResponse":
        if self.status == "needs_selection":
            if self.selected_text or self.alternatives or self.recommendation:
                raise ValueError("needs_selection response must not contain alternatives")
            if not self.follow_up_questions or len(self.follow_up_questions) > 1:
                raise ValueError("needs_selection response requires one follow-up question")
            return self
        if len(self.alternatives) != 5:
            raise ValueError("exactly five alternatives are required")
        tones = [item.tone for item in self.alternatives]
        if set(tones) != set(TONES) or len(tones) != len(set(tones)):
            raise ValueError("each tone must appear exactly once")
        terms = [item.word_or_phrase.casefold() for item in self.alternatives]
        if len(terms) != len(set(terms)):
            raise ValueError("alternatives must be distinct")
        if self.recommendation is None or self.recommendation.selected_alternative.casefold() not in terms:
            raise ValueError("recommendation must reference an alternative")
        if not self.selected_text or not self.interpreted_meaning or not self.part_of_speech:
            raise ValueError("valid response requires interpretation fields")
        if len(self.follow_up_questions) > 1:
            raise ValueError("at most one follow-up question is allowed")
        return self


class SynonymProviderResponse(BaseModel):
    """Model-authored fields; host-owned selection and context are added later."""

    model_config = ConfigDict(extra="forbid", strict=True)

    interpreted_meaning: str = Field(min_length=1, max_length=2000)
    part_of_speech: str = Field(min_length=1, max_length=128)
    alternatives: list[SynonymAlternative]
    recommendation: SynonymRecommendation
    limitation: str | None = Field(default=None, max_length=2000)
    follow_up_questions: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_model_result(self) -> "SynonymProviderResponse":
        if len(self.alternatives) != 5:
            raise ValueError("exactly five alternatives are required")
        tones = [item.tone for item in self.alternatives]
        if set(tones) != set(TONES) or len(tones) != len(set(tones)):
            raise ValueError("each tone must appear exactly once")
        terms = [item.word_or_phrase.casefold() for item in self.alternatives]
        if len(terms) != len(set(terms)):
            raise ValueError("alternatives must be distinct")
        if self.recommendation.selected_alternative.casefold() not in terms:
            raise ValueError("recommendation must reference an alternative")
        if len(self.follow_up_questions) > 1:
            raise ValueError("at most one follow-up question is allowed")
        return self
