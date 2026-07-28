"""Unit tests: LanguageTool adapter (grammar extra).

Ported prototype regression (local-server/server.py): the pinned
language-tool-python release exposes snake_case ``error_length`` and
``rule_id`` — the camelCase spellings do not exist. These tests run
against real ``Match`` objects (no Java process needed) and skip when
the grammar extra is not installed.
"""
from __future__ import annotations

import pytest

language_tool_python = pytest.importorskip("language_tool_python")

from language_tool_python.match import Match

from audisor_assistant.application.languagetool_adapter import LanguageToolChecker
from audisor_assistant.application.fix_engines import GrammarMatch


def _lt_match(offset: int, length: int, text: str, replacements: list[str]) -> Match:
    return Match(
        {
            "message": "Possible spelling mistake found.",
            "shortMessage": "Spelling mistake",
            "replacements": [{"value": value} for value in replacements],
            "offset": offset,
            "length": length,
            "context": {"text": text, "offset": offset, "length": length},
            "sentence": text,
            "rule": {
                "id": "MORFOLOGIK_RULE_EN_US",
                "issueType": "misspelling",
                "category": {"id": "TYPOS"},
            },
            "type": {"typeName": "Other"},
        },
        text,
    )


class _StubTool:
    """Stands in for language_tool_python.LanguageTool (no Java)."""

    def __init__(self, matches):
        self._matches = matches

    def check(self, text: str):
        return self._matches


def test_match_exposes_snake_case_error_length_not_camel_case():
    match = _lt_match(0, 3, "Teh cat", ["The"])
    # Prototype regression: these are the only valid spellings in 3.4.0.
    assert match.error_length == 3
    assert match.rule_id == "MORFOLOGIK_RULE_EN_US"
    assert not hasattr(match, "errorLength")
    assert not hasattr(match, "ruleId")


def test_adapter_maps_matches_to_grammar_matches():
    text = "Teh cat sat on teh mat"
    checker = LanguageToolChecker(
        _StubTool(
            [
                _lt_match(0, 3, text, ["The", "Ten"]),
                _lt_match(15, 3, text, ["the"]),
            ]
        )
    )
    matches = checker.check(text)
    assert matches == [
        GrammarMatch(
            offset=0,
            length=3,
            rule_id="MORFOLOGIK_RULE_EN_US",
            message="Possible spelling mistake found.",
            replacements=("The", "Ten"),
        ),
        GrammarMatch(
            offset=15,
            length=3,
            rule_id="MORFOLOGIK_RULE_EN_US",
            message="Possible spelling mistake found.",
            replacements=("the",),
        ),
    ]


def test_adapter_replacements_are_plain_strings():
    checker = LanguageToolChecker(_StubTool([_lt_match(0, 3, "Teh", ["The"])]))
    (match,) = checker.check("Teh")
    assert all(isinstance(item, str) for item in match.replacements)
