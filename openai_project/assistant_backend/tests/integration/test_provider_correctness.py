"""Regression tests for the repeated-output provider defect.

Proven root cause: a stale server process configured with the
``fake-deterministic`` provider served byte-identical canned results for
every input while the UI gave no provenance signal.  These tests pin the
correctness contract:

1. fake-provider visibility — the envelope always reports the fake
   provider so it can never masquerade as a real model;
2. canned-output exclusion — when the reported provider is
   ``local-openai-compatible`` the known fake fixture strings must not
   appear, and the result must correspond to the submitted input;
3. request/result correlation — each response echoes its own request_id
   and carries a result derived from its own input.

No "identical outputs are impossible" assertion is made: a real model may
legitimately repeat itself; only fixture-correspondence is asserted.
"""
from __future__ import annotations

import json

from fastapi.testclient import TestClient

from audisor_assistant.application.service import AssistantService
from audisor_assistant.auth.development import DEV_IDENTITY_HEADER
from audisor_assistant.domain.modes import AssistantMode
from audisor_assistant.main import create_app
from audisor_assistant.providers.base import (
    CompletionReply,
    CompletionRequest,
    DeterministicFakeProvider,
    ProviderCapabilities,
    _FAKE_RESULTS,
)

DEV_HEADERS = {DEV_IDENTITY_HEADER: "dev-user"}


def _canned_strings() -> list[str]:
    """Every literal string in the fake fixtures long enough to be a
    meaningful canned-output marker."""
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


def _source_text(request: CompletionRequest) -> str:
    return request.user_prompt.split("TEXT:\n", 1)[-1].split("\n\n", 1)[0].strip()


class _EchoLocalProvider:
    """Stub with the real local provider's identity that derives every
    result from the submitted input — the shape a correct provider has."""

    provider_id = "local-openai-compatible"
    source = "local"
    capabilities = ProviderCapabilities()

    def complete(self, request: CompletionRequest) -> CompletionReply:
        source = _source_text(request)
        payloads: dict[AssistantMode, dict] = {
            AssistantMode.FIX_WORDING: {
                "corrected_text": source,
                "changes": [],
                "no_changes_needed": True,
            },
            AssistantMode.DRAFT_THREE_REPLIES: {
                "in_short": f"About: {source}",
                "brief": f"Re: {source}",
                "thorough": f"Regarding: {source}",
                "diplomatic": f"Concerning: {source}",
                "message_purpose": "Respond to the submitted message.",
                "tone": "neutral",
                "uncertainty": [],
            },
            AssistantMode.TEACH_CLEARLY: {
                "basics": f"Core idea: {source}",
                "why_this_matters": "It grounds the explanation in the input.",
            },
            AssistantMode.EXPAND_IDEA: {
                "expanded_text": f"{source} — expanded.",
                "preserved_intent": f"Preserves the point of: {source}",
            },
            AssistantMode.TRANSLATE_SLANG_JARGON: {
                "term": source[:40] or "term",
                "professional_translation": f"Professional form of: {source}",
                "plain_meaning": f"Plain meaning of: {source}",
                "origin_context": "Test fixture.",
                "example": {"original": source, "professional": source},
            },
            AssistantMode.VISUALIZE_DESIGN: {
                "diagram_code": "flowchart TD\n  A[Input] --> B[Output]",
                "summary": f"Diagram for: {source}",
                "builder_prompt": f"Build: {source}",
            },
        }
        return CompletionReply(
            text=json.dumps(payloads[request.mode]),
            usage={"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
        )


def _client(provider) -> TestClient:
    return TestClient(create_app(AssistantService(provider)))


def _post(client: TestClient, request_id: str, mode: str, text: str, **extra):
    payload = {"request_id": request_id, "mode": mode, "text": text}
    payload.update(extra)
    return client.post("/v1/assistant/requests", json=payload, headers=DEV_HEADERS)


def test_fake_provider_is_always_visible_in_envelope(monkeypatch):
    monkeypatch.setenv("AUDISOR_ASSISTANT_ENV", "test")
    response = _post(
        _client(DeterministicFakeProvider()),
        "vis-1",
        "teach_clearly",
        "Any input at all.",
    )
    assert response.status_code == 200
    assert response.json()["provider"] == {
        "id": "fake-deterministic",
        "source": "local",
    }


def test_canned_outputs_excluded_when_provider_is_local(monkeypatch):
    """G8: known fake fixture strings must never appear in a response whose
    reported provider is local-openai-compatible, and each result must
    contain its own input-specific concept."""
    monkeypatch.setenv("AUDISOR_ASSISTANT_ENV", "test")
    client = _client(_EchoLocalProvider())
    fixtures = {
        "draft_three_replies": "Vendor asks to move Friday deployment to Monday.",
        "teach_clearly": "A mutex prevents concurrent entry to a critical section.",
        "expand_idea": "Cache corrections per paragraph hash to skip rechecks.",
        "fix_wording": "this sentense has a typo in it",
    }
    canned = _canned_strings()
    assert canned, "fixture extraction must find canned strings"
    for mode, text in fixtures.items():
        response = _post(client, f"g8-{mode}", mode, text)
        assert response.status_code == 200
        body = response.json()
        assert body["provider"]["id"] == "local-openai-compatible"
        result_text = json.dumps(body["result"])
        for phrase in canned:
            assert phrase not in result_text, (mode, phrase)
        # input correspondence, not mere divergence
        marker = text.split()[0 if mode != "draft_three_replies" else 6]
        assert marker.rstrip(".") in result_text, mode


def test_request_and_result_correlate_per_request(monkeypatch):
    monkeypatch.setenv("AUDISOR_ASSISTANT_ENV", "test")
    client = _client(_EchoLocalProvider())
    first = _post(client, "corr-1", "expand_idea", "First unique input about caching.")
    second = _post(client, "corr-2", "expand_idea", "Second unique input about badges.")
    body_1, body_2 = first.json(), second.json()
    assert body_1["request_id"] == "corr-1"
    assert body_2["request_id"] == "corr-2"
    assert "caching" in json.dumps(body_1["result"])
    assert "badges" in json.dumps(body_2["result"])
    assert body_1["result"] != body_2["result"]
    assert body_1["usage"]["total_tokens"] > 0
