"""Public request/response schemas for POST /v1/chat.

A dedicated operator-chat endpoint that returns real model replies
with provider-reported token usage.  Unknown fields are rejected.

**Multi-turn history contract (completed-turn invariant):**

History represents completed conversation turns only.  This is a UI
conversation invariant, not a universal chat rule — it enforces that
the client sends an ordered, well-formed record of past exchanges.

- The server owns the system prompt.  Clients cannot supply, replace, or
  extend it through the request body.
- ``history`` carries prior user and assistant turns in chronological order.
- Supported roles: ``"user"`` and ``"assistant"`` only; any other role
  (including ``"system"``) is rejected with HTTP 422.
- Strict alternation: user speaks first, roles alternate, and non-empty
  history must end with ``"assistant"`` (the last completed turn before
  the new draft).  Adjacent messages with the same role are rejected.
- ``message`` is the current (latest) user turn appended to the history
  before forwarding to the provider.  Because history must end with
  ``"assistant"``, this avoids two consecutive user turns in the
  provider’s message array.
- Request-size bounds: each message content up to MAX_TEXT_CHARS (20 000)
  characters; history limited to MAX_HISTORY_MESSAGES (200) items;
  aggregate request text (all history content + current message) must
  not exceed MAX_CHAT_REQUEST_CHARS.
  Exceeding any bound returns HTTP 422 with an explicit error.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..policies.limits import MAX_MODEL_CHARS, MAX_TEXT_CHARS, MODEL_NAME_PATTERN
from ..schemas.responses import ProviderInfo

#: Upper bound on prior turns accepted per request — payload guard only.
MAX_HISTORY_MESSAGES = 200

#: Aggregate character cap across all history content + current message.
#: Limits total request text to ~500 000 chars regardless of per-message caps.
MAX_CHAT_REQUEST_CHARS = 500_000


class ChatHistoryMessage(BaseModel):
    """One prior conversation turn the client will send with the request."""

    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=MAX_TEXT_CHARS)


def _validate_history_ordering(
    history: list[ChatHistoryMessage],
) -> list[ChatHistoryMessage]:
    """Ensure history represents completed turns: user first, alternating,
    last role is assistant (so the appended current message doesn't create
    two consecutive user turns).

    This is the completed-turn UI invariant.  Raises ValueError (surfaced
    as HTTP 422) on:

    - first message not from user;
    - two consecutive messages with the same role;
    - non-empty history that does not end with assistant.
    """
    if not history:
        return history
    if history[0].role != "user":
        raise ValueError(
            "History must begin with a 'user' message; "
            f"received '{history[0].role}' at position 0."
        )
    for idx in range(1, len(history)):
        if history[idx].role == history[idx - 1].role:
            raise ValueError(
                f"History must alternate roles; received two consecutive "
                f"'{history[idx].role}' messages at positions {idx - 1} and {idx}."
            )
    if history[-1].role != "assistant":
        raise ValueError(
            "Non-empty history must end with an 'assistant' message "
            "(the last completed turn before the new draft); "
            f"received '{history[-1].role}' at position {len(history) - 1}."
        )
    return history


def _validate_aggregate_size(
    message: str, history: list[ChatHistoryMessage]
) -> None:
    """Reject requests whose total text exceeds the aggregate cap."""
    total = len(message) + sum(len(h.content) for h in history)
    if total > MAX_CHAT_REQUEST_CHARS:
        raise ValueError(
            f"Aggregate request text ({total:,} characters) exceeds the "
            f"maximum of {MAX_CHAT_REQUEST_CHARS:,} characters."
        )


class ChatRequest(BaseModel):
    """Body for POST /v1/chat.

    ``message`` is the current user turn.  ``history`` carries the ordered
    prior completed turns (user first, alternating, ending with assistant).
    The server prepends its own system prompt — clients cannot supply one.
    """

    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=MAX_TEXT_CHARS)
    model: str | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_MODEL_CHARS,
        pattern=MODEL_NAME_PATTERN,
    )
    history: list[ChatHistoryMessage] = Field(
        default_factory=list, max_length=MAX_HISTORY_MESSAGES
    )

    @model_validator(mode="after")
    def _validate_history(self) -> "ChatRequest":
        _validate_history_ordering(self.history)
        _validate_aggregate_size(self.message, self.history)
        return self


class ChatUsage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    cost: float | None = None
    provider_type: Literal["local", "cloud"]


class ChatResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reply: str
    provider: ProviderInfo
    model: str
    usage: ChatUsage


class ChatErrorResponse(BaseModel):
    """Structured error when the provider is unavailable or misconfigured."""

    model_config = ConfigDict(extra="forbid")

    error: str
    category: str


class ChatEstimateRequest(BaseModel):
    """Preview of the next /v1/chat request for the composer capacity meter.

    ``message`` may be empty: an empty draft still costs the system prompt
    and any history that will be sent.
    """

    model_config = ConfigDict(extra="forbid")

    message: str = Field(default="", max_length=MAX_TEXT_CHARS)
    model: str | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_MODEL_CHARS,
        pattern=MODEL_NAME_PATTERN,
    )
    history: list[ChatHistoryMessage] = Field(
        default_factory=list, max_length=MAX_HISTORY_MESSAGES
    )

    @model_validator(mode="after")
    def _validate_history(self) -> "ChatEstimateRequest":
        _validate_history_ordering(self.history)
        _validate_aggregate_size(self.message, self.history)
        return self


class ChatEstimateResponse(BaseModel):
    """Approximate input estimate plus the authoritative context limit.

    ``context_limit`` and ``usable_input_tokens`` are ``None`` when the
    provider cannot report a context window — an explicit unknown state,
    never a guessed value.
    """

    model_config = ConfigDict(extra="forbid")

    estimated_input_tokens: int = Field(ge=0)
    reserved_output_tokens: int = Field(ge=0)
    context_limit: int | None = Field(default=None, ge=1)
    usable_input_tokens: int | None = Field(default=None, ge=0)
    model: str
    method: str
    confidence: str
