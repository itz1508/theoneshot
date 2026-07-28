"""Optional live smoke test against a local OpenAI-compatible server.

Never runs by default: requires the ``live_local`` marker to be selected
explicitly (``pytest -m live_local``) and a reachable local model server
configured through the standard environment variables.
"""
from __future__ import annotations

import os

import pytest

from audisor_assistant.application.service import AssistantService
from audisor_assistant.providers.local_openai_compatible import (
    LocalOpenAICompatibleProvider,
)
from audisor_assistant.schemas.requests import AssistantRequest

pytestmark = pytest.mark.live_local


@pytest.mark.skipif(
    not os.environ.get("AUDISOR_MODEL_ID"),
    reason="AUDISOR_MODEL_ID not set; live local smoke not available",
)
def test_live_local_fix_wording_smoke():
    service = AssistantService(LocalOpenAICompatibleProvider())
    request = AssistantRequest.model_validate(
        {
            "request_id": "live-1",
            "mode": "fix_wording",
            "text": "i has went to the store yesterday and buyed milk",
        }
    )
    response = service.handle(request)
    # A reachable server must produce a normalized envelope either way;
    # unreachable/misconfigured servers surface as failed, never raise.
    assert response.status.value in {"completed", "uncertainty", "failed"}
