"""Opt-in semantic correctness fixtures against a live local model.

Never runs by default: select explicitly with ``pytest -m live_semantic``
and set ``AUDISOR_MODEL_ID`` (plus the usual base-url variables) for a
reachable local OpenAI-compatible server.

These pin the G6 semantics that the deterministic suite cannot: a real
model, given a concrete input, must produce a result that corresponds to
that input (fixture concept present), reports real usage, and contains
none of the fake provider's canned strings.  A ``completed`` or
``uncertainty`` status is accepted; ``failed`` fails the fixture because
it means the live environment is broken, not that semantics regressed.
"""
from __future__ import annotations

import json
import os

import pytest

from audisor_assistant.application.service import AssistantService
from audisor_assistant.providers.base import _FAKE_RESULTS
from audisor_assistant.providers.local_openai_compatible import (
    LocalOpenAICompatibleProvider,
)
from audisor_assistant.schemas.requests import AssistantRequest


def _canned_strings() -> list[str]:
    found: list[str] = []

    def walk(value) -> None:
        if isinstance(value, str):
            if len(value) >= 15:
                found.append(value)
        elif isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    for payload in _FAKE_RESULTS.values():
        walk(payload)
    return found

pytestmark = [
    pytest.mark.live_semantic,
    pytest.mark.skipif(
        not os.environ.get("AUDISOR_MODEL_ID"),
        reason="AUDISOR_MODEL_ID not set; live semantic fixtures not available",
    ),
]

FIXTURES = [
    (
        "draft_three_replies",
        "Vendor asks to move Friday deployment to Monday because QA is incomplete.",
        "monday",
    ),
    (
        "teach_clearly",
        "A mutex prevents two threads from entering a critical section at once.",
        "mutex",
    ),
    (
        "expand_idea",
        "Cache grammar corrections per paragraph hash to avoid rechecking text.",
        # stem: matches cache / caching / cached
        "cach",
    ),
]


def _run(request_id: str, mode: str, text: str):
    service = AssistantService(LocalOpenAICompatibleProvider())
    request = AssistantRequest.model_validate(
        {"request_id": request_id, "mode": mode, "text": text}
    )
    return service.handle(request)


@pytest.mark.parametrize("mode,text,concept", FIXTURES)
def test_live_result_corresponds_to_input(mode, text, concept):
    response = _run(f"sem-{mode}", mode, text)
    assert response.status.value in {"completed", "uncertainty"}, response.result
    assert response.provider is not None
    assert response.provider.id == "local-openai-compatible"
    assert response.usage is not None and response.usage.get("total_tokens", 0) > 0
    result_text = json.dumps(response.result, default=str).lower()
    assert concept in result_text, (mode, concept)
    for phrase in _canned_strings():
        assert phrase.lower() not in result_text, (mode, phrase)


def test_live_distinct_inputs_yield_distinct_results():
    first = _run("sem-div-1", "expand_idea", FIXTURES[2][1])
    second = _run(
        "sem-div-2",
        "expand_idea",
        "Add a provenance badge to the assistant UI showing the active provider.",
    )
    assert first.status.value in {"completed", "uncertainty"}
    assert second.status.value in {"completed", "uncertainty"}
    assert first.result != second.result
