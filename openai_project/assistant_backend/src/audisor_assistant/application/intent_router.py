"""Intent router — classifies chat messages as informational or coding tasks.

Not every chat message should become a governed coding operation.
The router applies heuristic classification to determine intent:

- informational: answered with a simple completion (no operation created)
- coding_task: routed to OperationController.accept()
- ambiguous: requests user confirmation before creating an operation
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any


class Intent(str, Enum):
    """Classified intent of a user message."""

    INFORMATIONAL = "informational"
    CODING_TASK = "coding_task"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True)
class IntentClassification:
    """Result of intent classification."""

    intent: Intent
    confidence: float  # 0.0 to 1.0
    reason: str

    @property
    def is_coding_task(self) -> bool:
        return self.intent == Intent.CODING_TASK

    @property
    def is_informational(self) -> bool:
        return self.intent == Intent.INFORMATIONAL


# Patterns strongly indicating a coding task
_CODING_PATTERNS = [
    r"\b(fix|repair|patch|resolve)\b.*\b(bug|error|issue|crash|failure)\b",
    r"\b(implement|create|build|add|write)\b.*\b(feature|function|class|module|component|endpoint|page)\b",
    r"\b(refactor|restructure|reorganize|rewrite|extract|inline)\b",
    r"\b(delete|remove|drop)\b.*\b(file|function|class|module|component)\b",
    r"\b(move|rename|migrate)\b.*\b(file|function|class|module)\b",
    r"\b(install|upgrade|update|add)\b.*\b(dependency|package|library)\b",
    r"\b(deploy|release|publish)\b",
    r"\b(test|spec)\b.*\b(write|add|create|fix)\b",
    r"\brun\b.*\b(migration|script|command)\b",
]

# Patterns strongly indicating an informational request
_INFORMATIONAL_PATTERNS = [
    r"^(what|how|why|when|where|who|which|explain|describe|tell me|show me)\b",
    r"\b(what does|what is|how does|how do|what are)\b",
    r"\b(mean|meaning|purpose|difference between)\b",
    r"^(can you explain|please explain|help me understand)\b",
    r"\b(today|date|time|weather)\b",
]

# Keywords that bias toward coding tasks (weaker signal)
_CODING_KEYWORDS = frozenset({
    "fix", "create", "implement", "refactor", "add", "delete",
    "move", "rename", "install", "deploy", "write", "update",
    "modify", "change", "patch", "build", "remove", "migrate",
})


def classify_intent(
    message: str,
    history: list[dict[str, Any]] | None = None,
) -> IntentClassification:
    """Classify a user message as informational, coding task, or ambiguous.

    Args:
        message: The user's chat message.
        history: Previous conversation turns (for context).

    Returns:
        IntentClassification with intent, confidence, and reason.
    """
    text = message.strip().lower()

    # Empty or very short messages are informational
    if len(text) < 5:
        return IntentClassification(
            intent=Intent.INFORMATIONAL,
            confidence=0.9,
            reason="message_too_short",
        )

    # Check informational patterns first
    for pattern in _INFORMATIONAL_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            # But check if it also contains coding keywords
            words = set(text.split())
            coding_word_count = len(words & _CODING_KEYWORDS)
            if coding_word_count >= 2:
                return IntentClassification(
                    intent=Intent.AMBIGUOUS,
                    confidence=0.5,
                    reason="informational_pattern_with_coding_keywords",
                )
            return IntentClassification(
                intent=Intent.INFORMATIONAL,
                confidence=0.8,
                reason="informational_pattern_match",
            )

    # Check coding patterns
    for pattern in _CODING_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return IntentClassification(
                intent=Intent.CODING_TASK,
                confidence=0.85,
                reason="coding_pattern_match",
            )

    # Count coding keywords
    words = set(text.split())
    coding_word_count = len(words & _CODING_KEYWORDS)

    # Check if history contains tool use (biases toward coding)
    history_has_tools = False
    if history:
        for turn in history:
            if turn.get("role") == "assistant" and "tool" in str(turn.get("content", "")).lower():
                history_has_tools = True
                break

    if coding_word_count >= 2 or (coding_word_count >= 1 and history_has_tools):
        return IntentClassification(
            intent=Intent.CODING_TASK,
            confidence=0.7,
            reason="coding_keywords_detected",
        )

    # Imperative sentences (commands) bias toward coding
    if text.endswith(".") or not text.endswith("?"):
        if coding_word_count >= 1:
            return IntentClassification(
                intent=Intent.AMBIGUOUS,
                confidence=0.5,
                reason="imperative_with_coding_keyword",
            )

    # Default: informational
    return IntentClassification(
        intent=Intent.INFORMATIONAL,
        confidence=0.6,
        reason="no_strong_signal",
    )
