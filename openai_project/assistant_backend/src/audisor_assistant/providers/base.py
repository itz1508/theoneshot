"""Provider protocol, normalized provider errors, and the deterministic
fake provider used by tests and fake-provider end-to-end runs.

Providers receive a structured completion request and return raw text.
They never see credentials from the request body and never choose
themselves — selection is server-side configuration only.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Literal, Mapping, Protocol, runtime_checkable, get_args

from ..domain.modes import AssistantMode
from ..schemas.responses import PublicErrorCategory

#: Allowed values for CompletionRequest.purpose.
CompletionPurpose = Literal["operator_chat"]
_ALLOWED_PURPOSES: frozenset[str] = frozenset(get_args(CompletionPurpose))

#: Allowed roles for prior-turn history messages.
HistoryRole = Literal["user", "assistant"]
_ALLOWED_HISTORY_ROLES: frozenset[str] = frozenset(get_args(HistoryRole))


@dataclass(frozen=True)
class HistoryMessage:
    """One prior conversation turn included in a completion request."""

    role: HistoryRole
    content: str

    def __post_init__(self) -> None:
        if self.role not in _ALLOWED_HISTORY_ROLES:
            raise ValueError(
                f"Unsupported history role: {self.role!r}; "
                f"allowed: {sorted(_ALLOWED_HISTORY_ROLES)}"
            )


class ProviderError(Exception):
    """Normalized provider failure carrying only a safe public category
    and a sanitized message."""

    def __init__(self, category: PublicErrorCategory, public_message: str) -> None:
        super().__init__(public_message)
        self.category = category
        self.public_message = public_message


@dataclass(frozen=True)
class CompletionRequest:
    """Structured request handed to a provider.

    ``mode`` identifies a writing-assistant mode (set by /v1/assistant).
    ``purpose`` identifies a non-assistant operation (e.g. "operator_chat").
    Exactly one of mode or purpose must be set; providing both or neither is
    a programming error and raises ValueError at construction.
    """

    system_prompt: str
    user_prompt: str
    max_tokens: int
    timeout_seconds: float
    mode: AssistantMode | None = None
    # Non-assistant operation identifier — constrained to CompletionPurpose.
    purpose: CompletionPurpose | None = None
    # Per-request model override: selects a model within the configured
    # provider only; it never selects the provider itself.
    model_override: str | None = None
    # Prior conversation turns sent before the current user prompt.
    # Empty for single-turn operations (all writing-assistant modes).
    history: tuple[HistoryMessage, ...] = ()

    def __post_init__(self) -> None:
        if (self.mode is None) == (self.purpose is None):
            raise ValueError(
                "Exactly one of mode or purpose must be provided, "
                f"got mode={self.mode!r}, purpose={self.purpose!r}"
            )
        if self.purpose is not None and self.purpose not in _ALLOWED_PURPOSES:
            raise ValueError(
                f"Unsupported completion purpose: {self.purpose!r}; "
                f"allowed: {sorted(_ALLOWED_PURPOSES)}"
            )


@dataclass(frozen=True)
class ModelListing:
    """What the model dropdown may offer for the active provider.

    ``reachable`` is True/False only when the provider was actually
    probed (local); cloud providers are never probed for listing, so
    they report ``None``.  Never carries credentials.
    """

    current_model: str
    available_models: list[str]
    reachable: bool | None = None


@dataclass(frozen=True)
class CompletionReply:
    """Raw provider text plus safe usage metadata.

    ``usage`` keeps the OpenAI-style compatibility values consumed by the
    existing envelope.  ``native_usage`` is a sanitized provider-native
    usage object for provider-specific accounting: usage metadata only —
    never the full provider response, prompt, output, headers,
    credentials, or request payload.  ``native_usage_invalid`` is True
    only when the provider reported usage but its native shape was
    malformed; accounting must then not silently fall back.  ``model`` is
    the effective model resolved and frozen at the provider boundary
    (after model_override handling).
    """

    text: str
    usage: dict[str, int] | None = None
    native_usage: dict[str, object] | None = None
    native_usage_invalid: bool = False
    model: str | None = None


@dataclass(frozen=True)
class ProviderCapabilities:
    modes: frozenset[AssistantMode] = field(
        default_factory=lambda: frozenset(AssistantMode)
    )
    max_input_chars: int = 40_000


@runtime_checkable
class AssistantProvider(Protocol):
    """Protocol every assistant provider implements."""

    provider_id: str
    source: str  # "local" | "cloud"
    capabilities: ProviderCapabilities

    def complete(self, request: CompletionRequest) -> CompletionReply:
        """Run one structured completion.  Raise ProviderError on failure."""
        ...

    def list_models(self) -> ModelListing:
        """Describe the models selectable within this provider."""
        ...

    def effective_model(self, model_override: str | None) -> str:
        """Resolve the model actually used for a request.  This is the
        single model-resolution point: usage accounting consumes it and
        never recomputes the override handling itself."""
        ...

    def context_window(self, model_override: str | None) -> int | None:
        """Authoritative context-window size (tokens) for the effective
        model, or ``None`` when the provider cannot report one.  ``None``
        must surface as an explicit \"unknown\" state — never a guessed
        or hard-coded universal value."""
        ...


_FAKE_RESULTS: dict[AssistantMode, dict] = {
    AssistantMode.FIX_WORDING: {
        "corrected_text": "This sentence is deterministic.",
        "changes": [
            {
                "original": "determnistic",
                "correction": "deterministic",
                "reason": "Spelling.",
                "intentional_possible": False,
            }
        ],
        "no_changes_needed": False,
    },
    AssistantMode.DRAFT_THREE_REPLIES: {
        "in_short": "They are asking for a decision.",
        "brief": "Thanks — option A works for us. Please proceed.",
        "thorough": "Thanks for laying out both options. Option A fits our "
        "timeline and budget, so please proceed with it. If constraints "
        "change we can revisit option B next quarter.",
        "diplomatic": "Thank you for the thoughtful comparison. We lean "
        "toward option A for now, and we'd welcome your view before "
        "finalizing.",
        "message_purpose": "Request for a decision between two options.",
        "tone": "professional",
        "uncertainty": [],
    },
    AssistantMode.TRANSLATE_SLANG_JARGON: {
        "term": "circle back",
        "professional_translation": "follow up later",
        "plain_meaning": "Return to this topic at a later time.",
        "origin_context": "Corporate meeting jargon.",
        "usage_notes": ["Common in office settings; can sound evasive."],
        "example": {
            "original": "Let's circle back on this.",
            "professional": "Let's follow up on this next week.",
        },
    },
    AssistantMode.TEACH_CLEARLY: {
        "basics": "Start with the smallest working definition.",
        "building_from_there": ["Add one concept at a time."],
        "key_insights": ["Simple models compose into complex behavior."],
        "common_misconceptions": ["More detail is not more clarity."],
        "why_this_matters": "Clear foundations prevent later confusion.",
        "check_your_understanding": ["Can you restate the basics in one sentence?"],
    },
    AssistantMode.EXPAND_IDEA: {
        "expanded_text": "The idea, expanded deterministically without new facts.",
        "preserved_intent": "The original meaning is kept.",
        "added_assumptions": ["Audience is technical."],
        "uncertainty": [],
    },
    AssistantMode.VISUALIZE_DESIGN: {
        "kind": "workflow",
        "collapsed": None,
        "expanded": None,
        "diagram_code": (
            "flowchart TD\n"
            "  subgraph Drivers\n    A[Client]\n  end\n"
            "  subgraph Inbound[Inbound Ports]\n    B[API]\n  end\n"
            "  subgraph Domain[Domain Core]\n    C[Orchestrator]\n  end\n"
            "  subgraph Outbound[Outbound Ports]\n    D[ProviderPort]\n  end\n"
            "  subgraph Driven[Driven Actors]\n    E[Provider]\n  end\n"
            "  A --> B --> C --> D --> E"
        ),
        "summary": "Client enters through the API port, flows through the "
        "domain core, exits via the provider port to the provider adapter.",
        "builder_prompt": "Build a client driver, an API inbound port, an "
        "orchestrator domain core, a provider outbound port, and a provider "
        "driven adapter.",
        "warnings": [],
    },
}


class DeterministicFakeProvider:
    """Deterministic provider for tests and offline end-to-end runs.

    Returns a fixed, contract-valid JSON payload per mode.  Never touches
    the network.
    """

    provider_id = "fake-deterministic"
    source = "local"
    capabilities = ProviderCapabilities()

    #: Deterministic context window reported by default; tests may pass
    #: ``context_window=None`` to exercise the explicit unknown state.
    DEFAULT_CONTEXT_WINDOW = 8192

    def __init__(
        self,
        overrides: Mapping[AssistantMode, dict] | None = None,
        *,
        context_window: int | None = DEFAULT_CONTEXT_WINDOW,
    ) -> None:
        self._overrides = dict(overrides or {})
        self._context_window = context_window

    def _estimate_usage(self, request: CompletionRequest, reply_text: str) -> dict:
        """Deterministic token estimate from content length (~4 chars/token).

        Applied uniformly regardless of mode or purpose.
        """
        history_chars = sum(len(turn.content) for turn in request.history)
        prompt_tokens = max(
            1,
            (len(request.system_prompt) + history_chars + len(request.user_prompt)) // 4,
        )
        completion_tokens = max(1, len(reply_text) // 4)
        return {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        }

    def complete(self, request: CompletionRequest) -> CompletionReply:
        if request.purpose is not None:
            # Non-writing purpose: deterministic echo.
            reply_text = f"This is a deterministic response to: {request.user_prompt[:50]}"
        else:
            # Writing-assistant mode: contract-valid JSON payload.
            source = request.user_prompt.split("TEXT:\n", 1)[-1].split("\n\n", 1)[0].strip()
            if request.mode is AssistantMode.FIX_WORDING:
                payload = self._overrides.get(
                    request.mode,
                    {
                        "corrected_text": source,
                        "changes": [],
                        "no_changes_needed": True,
                        "inferred_intent": "Communicate the submitted message clearly.",
                        "tone": "neutral",
                        "context": "The message was supplied without additional context.",
                        "assumptions": [],
                        "uncertainty": [],
                    },
                )
            elif request.mode is AssistantMode.DRAFT_THREE_REPLIES:
                payload = self._overrides.get(
                    request.mode,
                    {
                        "in_short": source,
                        "brief": source,
                        "thorough": source,
                        "diplomatic": source,
                        "message_purpose": "Respond to the submitted message.",
                        "tone": "neutral",
                        "uncertainty": [],
                    },
                )
            else:
                payload = self._overrides.get(request.mode, _FAKE_RESULTS[request.mode])
            reply_text = json.dumps(payload)

        usage = self._estimate_usage(request, reply_text)
        return CompletionReply(
            text=reply_text,
            usage=usage,
            native_usage=dict(usage),
            model=self.effective_model(request.model_override),
        )

    def effective_model(self, model_override: str | None) -> str:
        return model_override or "fake-deterministic"

    def context_window(self, model_override: str | None) -> int | None:
        return self._context_window

    def list_models(self) -> ModelListing:
        return ModelListing(
            current_model="fake-deterministic",
            available_models=["fake-deterministic"],
            reachable=True,
        )
