import json

import pytest
from fastapi.testclient import TestClient

from audisor.api.synonyms import get_synonym_service
from audisor.main import create_app
from audisor.routing.router import ProviderRouter
from audisor.schemas.synonyms import SynonymProviderResponse, SynonymRequest, SynonymResponse
from audisor.synonyms import SynonymService, render_synonym_response
from audisor.workers.base import ProviderInvalidResponseError, ProviderUnavailableError
from audisor.schemas.task_input import TaskInput
from audisor.schemas.task_output import TaskOutput

from provider_testkit import provider_router


def model_payload(*, recommendation: str = "significant", tones=None, terms=None, extra=None):
    tones = tones or ["casual", "conversational", "neutral", "formal", "academic"]
    terms = terms or ["big", "important", "significant", "substantial", "consequential"]
    alternatives = [
        {
            "word_or_phrase": term,
            "tone": tone,
            "usage_guidance": f"Use {term} in this tone.",
            "meaning_difference": "It preserves the meaning in this context.",
            "connotation_warning": None,
        }
        for term, tone in zip(terms, tones)
    ]
    payload = {
        "interpreted_meaning": "Having strong importance.",
        "part_of_speech": "adjective",
        "alternatives": alternatives,
        "recommendation": {"selected_alternative": recommendation, "reason": "It preserves the context clearly."},
        "limitation": None,
        "follow_up_questions": [],
    }
    if extra:
        payload.update(extra)
    return payload


class FakeWorker:
    def __init__(self, answer, error=None):
        self.answer = answer
        self.error = error
        self.calls: list[TaskInput] = []

    def execute(self, task: TaskInput) -> TaskOutput:
        self.calls.append(task)
        if self.error:
            raise self.error
        answer = self.answer if isinstance(self.answer, str) else json.dumps(self.answer)
        return TaskOutput(task_id=task.task_id, answer=answer)


def service_for(worker: FakeWorker) -> SynonymService:
    return SynonymService(provider_router("local-openai-compatible", local=worker))


def test_adjective_result_preserves_context_and_renders_sentence_case():
    worker = FakeWorker(model_payload())
    result = service_for(worker).generate(SynonymRequest(selected_text="important", surrounding_text="This is an important requirement."))
    assert result.status == "ready"
    assert len(result.alternatives) == 5
    assert {item.tone for item in result.alternatives} == {"casual", "conversational", "neutral", "formal", "academic"}
    assert result.recommendation.selected_alternative == "significant"
    rendered = render_synonym_response(result)
    assert "Alternatives" in rendered
    assert "Recommendation:" in rendered
    assert "CASUAL" not in rendered
    assert "score" not in rendered.casefold()


@pytest.mark.parametrize("selected,context", [("run", "They run the process."), ("plan", "The plan changed."), ("light", "The room has light." )])
def test_verb_noun_and_context_dependent_word_are_sent_to_one_prompt(selected, context):
    worker = FakeWorker(model_payload())
    service_for(worker).generate(SynonymRequest(selected_text=selected, surrounding_text=context))
    assert selected in worker.calls[0].prompt
    assert context in worker.calls[0].prompt


def test_phrase_selection_uses_phrase_language_and_does_not_call_it_a_word():
    worker = FakeWorker(model_payload(terms=["highly important", "very relevant", "of consequence", "material to", "substantively significant"], recommendation="of consequence"))
    service_for(worker).generate(SynonymRequest(selected_text="very important", surrounding_text="This is very important."))
    assert "phrase" in worker.calls[0].prompt


def test_no_selection_returns_explicit_response_without_model_call():
    worker = FakeWorker(model_payload())
    result = service_for(worker).generate(SynonymRequest(selected_text="", surrounding_text="A sentence."))
    assert result.status == "needs_selection"
    assert len(result.follow_up_questions) == 1
    assert worker.calls == []
    assert "highlight" in render_synonym_response(result).casefold()


def test_missing_context_discloses_limitation_without_inventing_sentence():
    worker = FakeWorker(model_payload())
    result = service_for(worker).generate(SynonymRequest(selected_text="important"))
    assert result.context == ""
    assert "missing context" in result.limitation.casefold()
    assert "surrounding text" in worker.calls[0].prompt.casefold()


def test_risky_connotation_is_preserved_and_academic_option_can_be_flagged():
    payload = model_payload()
    payload["alternatives"][0]["connotation_warning"] = "This can sound dismissive in a professional setting."
    payload["alternatives"][4]["meaning_difference"] = "This elevated option may sound unnatural in everyday prose."
    result = service_for(FakeWorker(payload)).generate(SynonymRequest(selected_text="important", surrounding_text="An important requirement."))
    assert "dismissive" in result.alternatives[0].connotation_warning
    assert "unnatural" in result.alternatives[4].meaning_difference


def test_duplicate_alternatives_are_rejected_when_no_natural_tone_variant_exists():
    with pytest.raises(ProviderInvalidResponseError, match="schema-invalid"):
        service_for(FakeWorker(model_payload(terms=["important"] * 5))).generate(
            SynonymRequest(selected_text="important", surrounding_text="An important requirement.")
        )


def test_missing_tone_and_unsupported_tone_are_rejected():
    with pytest.raises(ProviderInvalidResponseError, match="schema-invalid"):
        service_for(FakeWorker(model_payload(tones=["casual", "conversational", "neutral", "formal"]))).generate(
            SynonymRequest(selected_text="important")
        )
    with pytest.raises(ProviderInvalidResponseError, match="schema-invalid"):
        service_for(FakeWorker(model_payload(tones=["casual", "conversational", "neutral", "formal", "technical"]))).generate(
            SynonymRequest(selected_text="important")
        )


def test_meaning_preservation_is_explicitly_model_owned_and_not_hidden_scoring():
    worker = FakeWorker(model_payload())
    service_for(worker).generate(SynonymRequest(selected_text="important", surrounding_text="An important requirement."))
    assert "preserve the intended meaning" in worker.calls[0].prompt.casefold()
    assert "self-evaluation" in worker.calls[0].prompt


@pytest.mark.parametrize(
    "payload,match",
    [
        ({"alternatives": []}, "schema-invalid"),
        ({"tones": ["casual", "casual", "neutral", "formal", "academic"]}, "schema-invalid"),
        ({"recommendation": "not-present"}, "schema-invalid"),
        ({"extra": {"internal_score": 93.91}}, "internal scoring"),
        ({"extra": {"follow_up_questions": ["One?", "Two?"]}}, "schema-invalid"),
    ],
)
def test_invalid_model_output_is_rejected(payload, match):
    base = model_payload(
        tones=payload.get("tones"),
        recommendation=payload.get("recommendation", "significant"),
        extra=payload.get("extra"),
    )
    if "alternatives" in payload:
        base["alternatives"] = payload["alternatives"]
    with pytest.raises(ProviderInvalidResponseError, match=match):
        service_for(FakeWorker(base)).generate(SynonymRequest(selected_text="important", surrounding_text="An important thing."))


@pytest.mark.parametrize("answer", ["not json", "```json\n{}\n```", '{"x": 1} {"y": 2}'])
def test_invalid_provider_framing_is_rejected(answer):
    with pytest.raises(ProviderInvalidResponseError):
        service_for(FakeWorker(answer)).generate(SynonymRequest(selected_text="important"))


def test_provider_failure_is_preserved():
    with pytest.raises(ProviderUnavailableError):
        service_for(FakeWorker(None, ProviderUnavailableError("offline"))).generate(SynonymRequest(selected_text="important"))


def test_single_word_phrase_alternative_requires_rewrite_explanation():
    payload = model_payload(terms=["very big", "important", "significant", "substantial", "consequential"])
    with pytest.raises(ProviderInvalidResponseError, match="unexplained phrase"):
        service_for(FakeWorker(payload)).generate(SynonymRequest(selected_text="important", surrounding_text="An important item."))


def test_schema_rejects_malformed_input_and_api_exposes_valid_response():
    with pytest.raises(ValueError):
        SynonymRequest(selected_text="word", selection_count=2)
    internal = SynonymProviderResponse.model_validate(model_payload())
    public = SynonymResponse(
        status="ready",
        selected_text="important",
        context="An important item.",
        interpreted_meaning=internal.interpreted_meaning,
        part_of_speech=internal.part_of_speech,
        alternatives=internal.alternatives,
        recommendation=internal.recommendation,
        limitation=internal.limitation,
        follow_up_questions=internal.follow_up_questions,
    )
    worker = FakeWorker(public.model_dump(mode="json"))
    app = create_app()
    app.dependency_overrides[get_synonym_service] = lambda: service_for(worker)
    response = TestClient(app).post("/v1/synonyms", json=[{"task_id": "t1", "prompt": "The highlighted word is important. Context: An important item."}])
    assert response.status_code == 200
    assert response.json()[0]["task_id"] == "t1"
    assert "Alternatives" in response.json()[0]["answer"]
    assert len(worker.calls) == 1

