"""Unit tests for CompletionRequest routing invariant.

Exactly one of ``mode`` or ``purpose`` must be provided.
Providing both or neither raises ValueError.
"""
from __future__ import annotations

import pytest

from audisor_assistant.domain.modes import AssistantMode
from audisor_assistant.providers.base import CompletionRequest


_COMMON = {
    "system_prompt": "system",
    "user_prompt": "user",
    "max_tokens": 64,
    "timeout_seconds": 5.0,
}


class TestRoutingInvariant:
    """CompletionRequest enforces exactly-one routing identity."""

    def test_mode_only_is_valid(self):
        req = CompletionRequest(mode=AssistantMode.FIX_WORDING, **_COMMON)
        assert req.mode is AssistantMode.FIX_WORDING
        assert req.purpose is None

    def test_purpose_only_is_valid(self):
        req = CompletionRequest(purpose="operator_chat", **_COMMON)
        assert req.purpose == "operator_chat"
        assert req.mode is None

    def test_neither_raises(self):
        with pytest.raises(ValueError, match="Exactly one"):
            CompletionRequest(**_COMMON)

    def test_both_raises(self):
        with pytest.raises(ValueError, match="Exactly one"):
            CompletionRequest(
                mode=AssistantMode.FIX_WORDING,
                purpose="operator_chat",
                **_COMMON,
            )


class TestPurposeValidation:
    """CompletionRequest rejects invalid purpose strings at runtime."""

    @pytest.mark.parametrize("bad_purpose", [
        "operator-chat",   # hyphen typo
        "chat",            # wrong value
        "operatorchat",    # no separator
        "oprator_chat",    # spelling typo
        "OPERATOR_CHAT",   # wrong case
    ])
    def test_invalid_purpose_rejected(self, bad_purpose):
        with pytest.raises(ValueError, match="Unsupported completion purpose"):
            CompletionRequest(purpose=bad_purpose, **_COMMON)

    def test_valid_purpose_accepted(self):
        req = CompletionRequest(purpose="operator_chat", **_COMMON)
        assert req.purpose == "operator_chat"
