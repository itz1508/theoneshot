"""Public request schema for POST /v1/assistant/requests.

Unknown fields are rejected (``extra="forbid"``).  Credentials are never
part of the request body; provider selection is server-side only.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from ..domain.modes import AssistantMode
from ..policies.limits import (
    MAX_CONTEXT_CHARS,
    MAX_ID_CHARS,
    MAX_MODEL_CHARS,
    MAX_SELECTED_TEXT_CHARS,
    MAX_TEXT_CHARS,
    MAX_TONE_CHARS,
    MODEL_NAME_PATTERN,
)


class AssistantRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1, max_length=MAX_ID_CHARS)
    mode: AssistantMode
    text: str = Field(min_length=1, max_length=MAX_TEXT_CHARS)
    selected_text: str | None = Field(default=None, max_length=MAX_SELECTED_TEXT_CHARS)
    context: str | None = Field(default=None, max_length=MAX_CONTEXT_CHARS)
    tone: str | None = Field(default=None, max_length=MAX_TONE_CHARS)
    workspace_id: str | None = Field(default=None, max_length=MAX_ID_CHARS)
    # Optional model override *within* the server-configured provider.
    # Provider selection itself is never client-controlled.
    model: str | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_MODEL_CHARS,
        pattern=MODEL_NAME_PATTERN,
    )
