"""Provider protocol, normalized provider errors, and the deterministic
fake provider used by tests and fake-provider end-to-end runs.

Providers receive a structured completion request and return raw text.
They never see credentials from the request body and never choose
themselves — selection is server-side configuration only.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Mapping, Protocol, runtime_checkable

from ..domain.modes import AssistantMode
from ..schemas.responses import PublicErrorCategory


class ProviderError(Exception):
    """Normalized provider failure carrying only a safe public category
    and a sanitized message."""

    def __init__(self, category: PublicErrorCategory, public_message: str) -> None:
        super().__init__(public_message)
        self.category = category
        self.public_message = public_message


@dataclass(frozen=True)
class CompletionRequest:
    """Structured request handed to a provider."""

    mode: AssistantMode
    system_prompt: str
    user_prompt: str
    max_tokens: int
    timeout_seconds: float
    # Per-request model override: selects a model within the configured
    # provider only; it never selects the provider itself.
    model_override: str | None = None


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
    """Raw provider text plus safe usage metadata."""

    text: str
    usage: dict[str, int] | None = None


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
        "diagram_code": "flowchart TD\n  A[Client] --> B[API]\n  B --> C[Provider]",
        "summary": "Client calls API which calls one provider.",
        "builder_prompt": "Build a client, an API, and one provider adapter.",
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

    def __init__(self, overrides: Mapping[AssistantMode, dict] | None = None) -> None:
        self._overrides = dict(overrides or {})

    def complete(self, request: CompletionRequest) -> CompletionReply:
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
        return CompletionReply(
            text=json.dumps(payload),
            usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        )

    def list_models(self) -> ModelListing:
        return ModelListing(
            current_model="fake-deterministic",
            available_models=["fake-deterministic"],
            reachable=True,
        )
