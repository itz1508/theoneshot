"""Prompt construction for the assistant modes.

Extracted from ``service.py`` so the orchestration module stays within
the repository size policy; the public surface (``build_system_prompt``,
``build_user_prompt``) is re-exported by ``service.py`` unchanged.
"""
from __future__ import annotations

from ..domain.modes import AssistantMode
from ..schemas.requests import AssistantRequest

_MODE_INSTRUCTIONS: dict[AssistantMode, str] = {
    AssistantMode.FIX_WORDING: (
        "Improve the user's full message, not just isolated words. Correct grammar "
        "and wording, clarify the likely intent, and preserve the user's voice. "
        "Preserve tone, rhythm, sentence fragments, and intentional informality. "
        "Do not polish wording that is already correct. "
        "Do not invent requirements or facts. Explain actual wording changes, then "
        "state the inferred intent, tone, context, assumptions, and uncertainty. "
        'Respond with only a JSON object: {"corrected_text": string, '
        '"changes": [{"original": string, "correction": string, "reason": string, '
        '"intentional_possible": boolean}], "no_changes_needed": boolean, '
        '"inferred_intent": string, "tone": string, "context": string, '
        '"assumptions": [string], "uncertainty": [string]}.'
    ),
    AssistantMode.DRAFT_THREE_REPLIES: (
        "Identify the central point of the message and what response is "
        "expected, then draft three usable replies: brief (roughly 2-3 "
        "sentences), thorough (addresses all material points), and diplomatic "
        "(careful and sensitive). If context is insufficient, state the "
        "limitation in the uncertainty list. "
        'Respond with only a JSON object: {"in_short": string, "brief": string, '
        '"thorough": string, "diplomatic": string, "message_purpose": string, '
        '"tone": string, "uncertainty": [string]}.'
    ),
    AssistantMode.TRANSLATE_SLANG_JARGON: (
        "Translate the selected slang or jargon term. Explain its meaning and "
        "social/emotional connotation, who commonly uses it, when the original "
        "is appropriate, and when the professional version is safer. If origin "
        "or usage is unclear, say so in usage_notes. "
        'Respond with only a JSON object: {"term": string, '
        '"professional_translation": string, "plain_meaning": string, '
        '"origin_context": string, "usage_notes": [string], '
        '"example": {"original": string, "professional": string}}.'
    ),
    AssistantMode.TEACH_CLEARLY: (
        "Teach the topic starting with prerequisites and the simplest "
        "explanation, progressing one concept at a time with concrete examples "
        "and analogies, including key insights and common misconceptions, and "
        "ending with a self-check. "
        'Respond with only a JSON object: {"basics": string, '
        '"building_from_there": [string], "key_insights": [string], '
        '"common_misconceptions": [string], "why_this_matters": string, '
        '"check_your_understanding": [string]}.'
    ),
    AssistantMode.EXPAND_IDEA: (
        "Expand the idea while remaining close to the user's meaning. Do not "
        "invent facts, names, commitments, or requirements. List every "
        "assumption you added rather than hiding it. "
        'Respond with only a JSON object: {"expanded_text": string, '
        '"preserved_intent": string, "added_assumptions": [string], '
        '"uncertainty": [string]}.'
    ),
    AssistantMode.VISUALIZE_DESIGN: (
        "First classify the user's description as exactly one kind: "
        "'layout' (a UI screen, page, or component arrangement), 'workflow' "
        "(a process, data flow, or system architecture), or 'unclear' (not "
        "enough concrete detail to draw anything). Use only components the "
        "user described or clearly implied; do not invent architecture. "
        "For 'layout': return collapsed (a short ASCII-tree overview, one "
        "string per line) and expanded (a detailed ASCII tree, one string "
        "per line); leave diagram_code null. For 'workflow': return Mermaid "
        "flowchart code in diagram_code arranged as a Hexagonal Architecture "
        "(ports-and-adapters) diagram with plain-text labels; leave collapsed "
        "and expanded null. For 'unclear': explain in summary exactly what is "
        "missing; leave the other fields null. Also return a builder prompt "
        "that reflects only the user's description (null when unclear). "
        "Hexagonal template for 'workflow': arrange the user's components "
        "into five layers — drivers (external callers) at the top, inbound "
        "ports (APIs/entry points) below them, the domain core in the "
        "middle, outbound ports (outgoing interfaces) below the core, and "
        "driven actors (databases, external services) at the bottom. Use "
        "Mermaid flowchart TD with subgraphs named 'Drivers', 'Inbound "
        "Ports', 'Domain Core', 'Outbound Ports', 'Driven Actors', and "
        "arrows flowing top-to-bottom through the layers. If the user did "
        "not describe a distinct port or domain, infer the minimal "
        "hexagonal skeleton (one entry, one core step, one exit) from the "
        "described flow without inventing extra components. "
        'Respond with only a JSON object: {"kind": "layout"|"workflow"|'
        '"unclear", "summary": string, "collapsed": [string]|null, '
        '"expanded": [string]|null, "diagram_code": string|null, '
        '"builder_prompt": string|null, "warnings": [string]}.'
    ),
}

_SYSTEM_PREAMBLE = (
    "You are the Audisor Writing & Design Assistant. Respond with a single "
    "JSON object and nothing else. Never include markdown fences, "
    "commentary, credentials, or file paths."
)


def build_system_prompt(mode: AssistantMode) -> str:
    return f"{_SYSTEM_PREAMBLE}\n\n{_MODE_INSTRUCTIONS[mode]}"


def build_user_prompt(request: AssistantRequest) -> str:
    parts = [f"TEXT:\n{request.text}"]
    if request.selected_text:
        parts.append(f"SELECTED TERM:\n{request.selected_text}")
    if request.context:
        parts.append(f"CONTEXT:\n{request.context}")
    if request.tone:
        parts.append(f"REQUESTED TONE:\n{request.tone}")
    return "\n\n".join(parts)
