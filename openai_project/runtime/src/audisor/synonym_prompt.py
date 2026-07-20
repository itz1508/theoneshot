"""Single authoritative model instruction for synonym generation."""

from __future__ import annotations

import json

from audisor.schemas.synonyms import SynonymRequest


def build_synonym_prompt(request: SynonymRequest) -> str:
    selection_kind = "phrase" if request.is_phrase else "single word"
    context = request.surrounding_text or "(no surrounding sentence was supplied)"
    limitation = (
        "State in limitation that the intended meaning may depend on missing context."
        if not request.surrounding_text
        else "Use the supplied context only."
    )
    return (
        "You are Audisor's synonym-generation assistant. Return exactly one JSON object, "
        "with no Markdown, prose outside JSON, scores, ratings, confidence scores, or self-evaluation. "
        f"The selection is a {selection_kind}. Identify its part of speech and meaning in context. "
        "Return exactly five distinct alternatives, one for each tone in this exact set: "
        "casual, conversational, neutral, formal, academic. Preserve the intended meaning and "
        "the part of speech for a single word. For every alternative provide word_or_phrase, tone, "
        "usage_guidance, meaning_difference, and connotation_warning (null when none). "
        "Recommend one returned alternative and explain why. Include at most one follow-up question. "
        f"{limitation} The JSON keys must be exactly: interpreted_meaning, part_of_speech, "
        "alternatives, recommendation, limitation, follow_up_questions.\n\n"
        f"Selected text: {json.dumps(request.selected_text, ensure_ascii=False)}\n"
        f"Surrounding text: {json.dumps(context, ensure_ascii=False)}\n"
        "Return JSON only."
    )


def build_synonym_task_prompt(task_prompt: str) -> str:
    """Wrap one existing Audisor task prompt without creating a new envelope."""
    return (
        "Generate context-aware synonyms for the user's request below. Return exactly one JSON object "
        "with the SynonymResponse fields: status, selected_text, context, interpreted_meaning, "
        "part_of_speech, alternatives, recommendation, limitation, and follow_up_questions. "
        "Use status=needs_selection when the request contains no highlighted word; in that case ask "
        "the user to highlight one word and return no alternatives. For a valid selection return exactly "
        "five alternatives with tones casual, conversational, neutral, formal, and academic. Do not "
        "include Markdown, scores, ratings, or self-evaluation. Return JSON only.\n\n"
        f"User task prompt:\n{task_prompt}\n"
    )
