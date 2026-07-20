"""Provider-neutral synonym generation service."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping

from pydantic import ValidationError

from audisor.routing.router import ProviderRouter
from audisor.schemas.task_input import TaskInput
from audisor.schemas.task_output import TaskOutput
from audisor.schemas.synonyms import (
    SynonymProviderResponse,
    SynonymRequest,
    SynonymResponse,
)
from audisor.workers.base import ProviderCapabilityError, ProviderInvalidResponseError, WorkerProvider
from audisor.synonym_prompt import build_synonym_prompt, build_synonym_task_prompt


_FORBIDDEN_MODEL_KEYS = {"score", "rating", "internal_score", "quality_score", "self_evaluation"}
_QUESTION_RE = re.compile(r"\?\s*$")


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _contains_forbidden_key(value: object) -> bool:
    if isinstance(value, Mapping):
        return any(key.casefold() in _FORBIDDEN_MODEL_KEYS or _contains_forbidden_key(item) for key, item in value.items())
    if isinstance(value, list):
        return any(_contains_forbidden_key(item) for item in value)
    return False


def _decode_provider_json(answer: object) -> dict[str, object]:
    if not isinstance(answer, str) or not answer.strip():
        raise ProviderInvalidResponseError("Synonym provider returned an empty response")
    text = answer.strip()
    if text.startswith("```"):
        raise ProviderInvalidResponseError("Synonym provider returned Markdown instead of JSON")
    try:
        value, end = json.JSONDecoder(object_pairs_hook=_reject_duplicate_keys).raw_decode(text)
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise ProviderInvalidResponseError("Synonym provider returned invalid JSON") from exc
    if text[end:].strip() or not isinstance(value, dict):
        raise ProviderInvalidResponseError("Synonym provider returned more than one JSON value")
    if _contains_forbidden_key(value):
        raise ProviderInvalidResponseError("Synonym provider exposed internal scoring")
    return value


def _validate_single_word_pos(request: SynonymRequest, result: SynonymProviderResponse) -> None:
    if request.is_phrase:
        return
    if any(" " in item.word_or_phrase.strip() for item in result.alternatives):
        if not any("sentence rewrite" in item.meaning_difference.casefold() for item in result.alternatives if " " in item.word_or_phrase.strip()):
            raise ProviderInvalidResponseError("A single-word selection received an unexplained phrase alternative")


def render_synonym_response(result: SynonymResponse) -> str:
    """Render one validated response using fixed sentence-case headings."""
    if result.status == "needs_selection":
        return "Please highlight one word to generate synonyms."
    lines = [f"The word: {result.selected_text}", f'Context: "{result.context}"', "", "Alternatives", ""]
    for index, alternative in enumerate(result.alternatives, start=1):
        warning = f" Connotation: {alternative.connotation_warning}." if alternative.connotation_warning else ""
        lines.append(
            f"{index}. {alternative.word_or_phrase} ({alternative.tone}) — "
            f"{alternative.usage_guidance} Meaning difference: {alternative.meaning_difference}.{warning}"
        )
        lines.append("")
    assert result.recommendation is not None
    lines.append(
        f"Recommendation: Given this context, use {result.recommendation.selected_alternative} "
        f"because {result.recommendation.reason}."
    )
    if result.limitation:
        lines.extend(["", f"Limitation: {result.limitation}"])
    if result.follow_up_questions:
        lines.extend(["", result.follow_up_questions[0]])
    return "\n".join(lines)


class SynonymService:
    def __init__(self, router: ProviderRouter) -> None:
        self._router = router

    @staticmethod
    def _no_selection(request: SynonymRequest) -> SynonymResponse:
        return SynonymResponse(
            status="needs_selection",
            selected_text="",
            context=request.surrounding_text or "",
            follow_up_questions=["Which word would you like alternatives for?"],
        )

    def generate(self, request: SynonymRequest) -> SynonymResponse:
        if request.is_empty:
            return self._no_selection(request)
        provider: WorkerProvider = self._router.select_provider()
        if not provider.capabilities().text:
            raise ProviderCapabilityError("Selected provider does not support text tasks", internal_detail="required=text")
        task = TaskInput(task_id=request.operation_id or "synonyms", prompt=build_synonym_prompt(request))
        output = provider.execute(task)
        try:
            value = _decode_provider_json(output.answer)
            result = SynonymProviderResponse.model_validate(value)
            _validate_single_word_pos(request, result)
        except (AttributeError, ValidationError) as exc:
            detail = "validation error" if isinstance(exc, ValidationError) else "missing response content"
            raise ProviderInvalidResponseError(
                f"Synonym provider returned schema-invalid output: {detail}"
            ) from exc
        context = request.surrounding_text or ""
        limitation = result.limitation
        if not context and not limitation:
            limitation = "No surrounding sentence was supplied; missing context may change the intended meaning."
        return SynonymResponse(
            status="ready",
            selected_text=request.selected_text,
            context=context,
            interpreted_meaning=result.interpreted_meaning,
            part_of_speech=result.part_of_speech,
            alternatives=result.alternatives,
            recommendation=result.recommendation,
            limitation=limitation,
            follow_up_questions=result.follow_up_questions,
        )

    def generate_task(self, task: TaskInput) -> TaskOutput:
        """Use the existing task_id/prompt/answer envelope for synonym work."""
        provider: WorkerProvider = self._router.select_provider()
        if not provider.capabilities().text:
            raise ProviderCapabilityError("Selected provider does not support text tasks", internal_detail="required=text")
        output = provider.execute(TaskInput(task_id=task.task_id, prompt=build_synonym_task_prompt(task.prompt)))
        try:
            value = _decode_provider_json(output.answer)
            result = SynonymResponse.model_validate(value)
        except (AttributeError, ValidationError) as exc:
            raise ProviderInvalidResponseError("Synonym provider returned schema-invalid output") from exc
        return TaskOutput(task_id=task.task_id, answer=render_synonym_response(result))
