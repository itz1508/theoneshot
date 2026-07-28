"""LanguageTool adapter (``grammar`` extra).

Imported lazily by :func:`fix_engines.build_grammar_state` — when the
``grammar`` extra (language-tool-python) is not installed this module
fails with ImportError and the caller reports a normalized configuration
state instead of crashing startup.

language-tool-python 3.4.0 exposes snake_case match attributes
(``error_length``, ``rule_id``); the camelCase spellings do not exist.
Initialization starts (and on first run downloads) a local LanguageTool
Java server; nothing is sent to remote services.
"""
from __future__ import annotations

import language_tool_python

from .fix_engines import GrammarMatch


class LanguageToolChecker:
    """GrammarChecker backed by a local LanguageTool (Java) process."""

    def __init__(self, tool: language_tool_python.LanguageTool) -> None:
        self._tool = tool

    def check(self, text: str) -> list[GrammarMatch]:
        return [
            GrammarMatch(
                offset=match.offset,
                # snake_case, not camelCase — errorLength/ruleId are absent
                # in the pinned release (prototype regression).
                length=match.error_length,
                rule_id=match.rule_id,
                message=match.message,
                replacements=tuple(match.replacements),
            )
            for match in self._tool.check(text)
        ]


def build_languagetool_checker(language: str = "en-US") -> LanguageToolChecker:
    """Eagerly start the local LanguageTool server (called only at
    startup, so requests never trigger a surprise download)."""
    return LanguageToolChecker(language_tool_python.LanguageTool(language))
