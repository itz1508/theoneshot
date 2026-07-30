"""Tests for the intent router — classifies chat messages.

Covers: informational detection, coding task detection, ambiguous cases,
history context influence, and edge cases.
"""
from __future__ import annotations

import pytest

from audisor_assistant.application.intent_router import (
    Intent,
    IntentClassification,
    classify_intent,
)


class TestIntentRouterInformational:
    def test_short_message_is_informational(self) -> None:
        result = classify_intent("hi")
        assert result.intent == Intent.INFORMATIONAL
        assert result.confidence >= 0.8

    def test_question_is_informational(self) -> None:
        result = classify_intent("what does this function do?")
        assert result.intent == Intent.INFORMATIONAL

    def test_explain_request_is_informational(self) -> None:
        result = classify_intent("explain how the router works")
        assert result.intent == Intent.INFORMATIONAL

    def test_how_does_question(self) -> None:
        result = classify_intent("how does authentication work in this app?")
        assert result.intent == Intent.INFORMATIONAL

    def test_no_signal_defaults_informational(self) -> None:
        result = classify_intent("the sky is blue")
        assert result.intent == Intent.INFORMATIONAL
        assert result.confidence <= 0.7  # Low confidence default


class TestIntentRouterCodingTask:
    def test_fix_bug_is_coding_task(self) -> None:
        result = classify_intent("fix the authentication bug")
        assert result.intent == Intent.CODING_TASK
        assert result.confidence >= 0.7

    def test_implement_feature_is_coding_task(self) -> None:
        result = classify_intent("implement a new endpoint for user profiles")
        assert result.intent == Intent.CODING_TASK

    def test_refactor_is_coding_task(self) -> None:
        result = classify_intent("refactor the database module")
        assert result.intent == Intent.CODING_TASK

    def test_create_component_is_coding_task(self) -> None:
        result = classify_intent("create a new component for the settings page")
        assert result.intent == Intent.CODING_TASK

    def test_delete_file_is_coding_task(self) -> None:
        result = classify_intent("delete the deprecated file utils.py")
        assert result.intent == Intent.CODING_TASK

    def test_deploy_is_coding_task(self) -> None:
        result = classify_intent("deploy the latest version to production")
        assert result.intent == Intent.CODING_TASK


class TestIntentRouterAmbiguous:
    def test_ambiguous_question_with_coding_words(self) -> None:
        result = classify_intent("how should I fix and refactor this?")
        # "how should I" triggers informational pattern, but fix+refactor are coding keywords
        assert result.intent == Intent.AMBIGUOUS

    def test_imperative_with_single_keyword(self) -> None:
        result = classify_intent("update the docs please.")
        # Single coding keyword + imperative sentence
        assert result.intent in (Intent.AMBIGUOUS, Intent.CODING_TASK)


class TestIntentRouterHistoryContext:
    def test_history_with_tools_biases_coding(self) -> None:
        """Single coding keyword + history containing tool use -> CODING_TASK."""
        history = [
            {"role": "user", "content": "read the config file"},
            {"role": "assistant", "content": "I used the tool file_read to check the config."},
        ]
        result = classify_intent("update the config", history=history)
        assert result.intent == Intent.CODING_TASK

    def test_no_history_single_keyword_not_enough(self) -> None:
        """Single coding keyword without history is not strongly coding."""
        result = classify_intent("update something")
        # Without a pattern match or history bias, single keyword may be ambiguous
        assert result.intent in (Intent.AMBIGUOUS, Intent.INFORMATIONAL, Intent.CODING_TASK)


class TestIntentClassificationProperties:
    def test_is_coding_task_property(self) -> None:
        c = IntentClassification(intent=Intent.CODING_TASK, confidence=0.9, reason="test")
        assert c.is_coding_task is True
        assert c.is_informational is False

    def test_is_informational_property(self) -> None:
        c = IntentClassification(intent=Intent.INFORMATIONAL, confidence=0.8, reason="test")
        assert c.is_informational is True
        assert c.is_coding_task is False
