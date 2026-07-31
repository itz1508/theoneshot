from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from audisor.audisor_lifecycle.management import (
    _load_probes,
    _probe_is_current,
    _run_probe,
    _save_probe,
    authorize_submission,
    invalidate_provider_readiness,
)
from audisor.audisor_lifecycle.stage_contracts import STAGE_OUTPUT_SCHEMAS
from audisor.audisor_lifecycle.stage_worker import ManagedStageWorker, _select_schema_mode
from audisor.schemas.task_output import TaskOutput
from audisor.workers.base import (
    ProviderCapabilities,
    ProviderContractInvalidError,
    ProviderResponseNotJsonError,
    ProviderSchemaUnsupportedError,
    ProviderUnavailableError,
    ProviderProcessReapError,
    ProviderPermanentRequestError,
)
from audisor.workers.fireworks import FireworksWorker
from audisor.workers.isolated_http import IsolatedHttpReapError, IsolatedHttpTimeout
from audisor.workers.local import LocalWorker

FIXTURES = Path(__file__).parent / "fixtures" / "aflow_repairs"


class _ReadyProvider:
    provider_id = "fixture-provider"
    timeout_seconds = 5.0
    model_id = "fixture-model"

    def __init__(self) -> None:
        self.full_calls = 0
        self.live_calls = 0
        self.adapter_version = "1"
        self.live_failure = False
        self.answer = '{"gaps":[]}'
        self.native = False
        self.reject_native = False
        self.structured_calls: list[dict[str, object]] = []

    def configuration_status(self) -> bool:
        return True

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            text=True, structured_output=True, native_json_schema=self.native
        )

    def readiness_identity(self) -> dict[str, object]:
        return {
            "provider_protocol": "fixture",
            "normalized_endpoint": "fixture://provider",
            "model_id": self.model_id,
            "structured_output_mode": "prompt_validated_json",
            "adapter_identity": "tests.ReadyProvider",
            "adapter_version": self.adapter_version,
            "behavior": {"mode": "deterministic"},
        }

    def run_full_readiness_probe(self) -> None:
        self.full_calls += 1

    def run_live_submission_check(self) -> None:
        self.live_calls += 1
        if self.live_failure:
            raise ProviderUnavailableError("fixture unavailable")

    def execute(self, task: object) -> TaskOutput:
        return TaskOutput(task_id=getattr(task, "task_id"), answer=self.answer)

    def execute_structured(self, task: object, **kwargs: object) -> TaskOutput:
        self.structured_calls.append(dict(kwargs))
        if kwargs["schema_mode"] == "native_json_schema" and self.reject_native:
            raise ProviderSchemaUnsupportedError("fixture native mode unsupported")
        return self.execute(task)


def _age_probe(tmp_path: Path, provider: _ReadyProvider, *, seconds: int) -> None:
    probes = _load_probes(tmp_path)
    record = probes["providers"][provider.provider_id]
    checked = datetime.now(timezone.utc) - timedelta(seconds=seconds)
    record["checked_at"] = checked.isoformat()
    record["expires_at"] = (checked + timedelta(seconds=300)).isoformat()
    record["generation"] += 1
    _save_probe(tmp_path, record)


def test_fresh_matching_readiness_uses_one_live_check(tmp_path: Path) -> None:
    provider = _ReadyProvider()
    _run_probe(provider, root=tmp_path, selection_reason="fixture")
    result = authorize_submission(provider, state_root=tmp_path)
    assert result["readiness_state"] == "current"
    assert provider.full_calls == 1
    assert provider.live_calls == 1


def test_fingerprint_change_requires_new_full_probe(tmp_path: Path) -> None:
    provider = _ReadyProvider()
    _run_probe(provider, root=tmp_path, selection_reason="fixture")
    provider.adapter_version = "2"
    result = authorize_submission(provider, state_root=tmp_path)
    assert result["outcome"] == "ready"
    assert provider.full_calls == 2
    assert provider.live_calls == 0


def test_live_failure_never_falls_back_to_stale_success(tmp_path: Path) -> None:
    provider = _ReadyProvider()
    _run_probe(provider, root=tmp_path, selection_reason="fixture")
    provider.live_failure = True
    result = authorize_submission(provider, state_root=tmp_path)
    assert result["readiness_state"] == "live_check_failed"
    assert result["outcome"] == "provider_unavailable"


def test_expired_probe_requires_requalification(tmp_path: Path) -> None:
    provider = _ReadyProvider()
    _run_probe(provider, root=tmp_path, selection_reason="fixture")
    _age_probe(tmp_path, provider, seconds=301)
    assert not _probe_is_current(_load_probes(tmp_path)["providers"][provider.provider_id], provider)


def test_expired_probe_runs_one_full_probe(tmp_path: Path) -> None:
    provider = _ReadyProvider()
    _run_probe(provider, root=tmp_path, selection_reason="fixture")
    _age_probe(tmp_path, provider, seconds=301)
    authorize_submission(provider, state_root=tmp_path)
    assert provider.full_calls == 2
    assert provider.live_calls == 0


@pytest.mark.parametrize("field", ["adapter_version", "model_id"])
def test_every_fingerprint_field_invalidates_probe(tmp_path: Path, field: str) -> None:
    provider = _ReadyProvider()
    _run_probe(provider, root=tmp_path, selection_reason="fixture")
    setattr(provider, field, "changed")
    assert not _probe_is_current(_load_probes(tmp_path)["providers"][provider.provider_id], provider)


def test_future_probe_timestamp_is_clock_invalid(tmp_path: Path) -> None:
    provider = _ReadyProvider()
    record = _run_probe(provider, root=tmp_path, selection_reason="fixture")
    future = datetime.now(timezone.utc) + timedelta(minutes=1)
    record.update(checked_at=future.isoformat(), expires_at=(future + timedelta(minutes=5)).isoformat(), generation=record["generation"] + 1)
    _save_probe(tmp_path, record)
    assert not _probe_is_current(_load_probes(tmp_path)["providers"][provider.provider_id], provider)


def test_stopped_provider_cannot_use_cached_probe(tmp_path: Path) -> None:
    test_live_failure_never_falls_back_to_stale_success(tmp_path)


def test_older_probe_cannot_overwrite_newer_invalidation(tmp_path: Path) -> None:
    provider = _ReadyProvider()
    old = _run_probe(provider, root=tmp_path, selection_reason="fixture")
    invalidate_provider_readiness(provider, root=tmp_path, reason="provider_timeout")
    _save_probe(tmp_path, old)
    current = _load_probes(tmp_path)["providers"][provider.provider_id]
    assert current["outcome"] == "invalidated"
    assert current["generation"] > old["generation"]


def test_missing_required_gap_field_preserves_validator_evidence() -> None:
    provider = _ReadyProvider()
    provider.answer = (FIXTURES / "schema" / "gap_finding_missing_gaps.json").read_text(encoding="utf-8")
    worker = ManagedStageWorker(primary=provider)
    with pytest.raises(ProviderContractInvalidError) as caught:
        worker.run_stage("gap_finding", {"artifact": "fixture"})
    detail = json.loads(caught.value.internal_detail)
    assert detail["json_path"] == "$"
    assert detail["validator_keyword"] == "required"
    assert "gaps" in detail["validator_message"]


def test_non_json_has_distinct_failure() -> None:
    provider = _ReadyProvider()
    provider.answer = (FIXTURES / "schema" / "gap_finding_not_json.txt").read_text(encoding="utf-8")
    with pytest.raises(ProviderResponseNotJsonError):
        ManagedStageWorker(primary=provider).run_stage("gap_finding", {"artifact": "fixture"})


def test_native_schema_mode_is_selected_only_for_compatible_schema() -> None:
    provider = _ReadyProvider()
    provider.capabilities = lambda: ProviderCapabilities(text=True, structured_output=True, native_json_schema=True)
    assert _select_schema_mode(provider, STAGE_OUTPUT_SCHEMAS["gap_finding"]) == "native_json_schema"
    incompatible = {"$ref": "#/$defs/value", "$defs": {"value": {"type": "object"}}}
    assert _select_schema_mode(provider, incompatible) == "prompt_validated_json"


def test_native_mode_sends_exact_stage_schema() -> None:
    provider = _ReadyProvider()
    provider.native = True
    ManagedStageWorker(primary=provider).run_stage("gap_finding", {"artifact": "fixture"})
    assert provider.structured_calls[0]["schema_mode"] == "native_json_schema"
    assert provider.structured_calls[0]["schema"] == STAGE_OUTPUT_SCHEMAS["gap_finding"]


def test_unsupported_native_mode_uses_prompt_validated_json() -> None:
    provider = _ReadyProvider()
    provider.native = True
    provider.reject_native = True
    ManagedStageWorker(primary=provider).run_stage("gap_finding", {"artifact": "fixture"})
    assert [call["schema_mode"] for call in provider.structured_calls] == [
        "native_json_schema", "prompt_validated_json"
    ]


def test_prompt_mode_contains_complete_schema() -> None:
    provider = _ReadyProvider()
    prompts: list[str] = []
    original = provider.execute_structured
    def capture(task: object, **kwargs: object) -> TaskOutput:
        prompts.append(getattr(task, "prompt"))
        return original(task, **kwargs)
    provider.execute_structured = capture
    ManagedStageWorker(primary=provider).run_stage("gap_finding", {"artifact": "fixture"})
    assert json.dumps(STAGE_OUTPUT_SCHEMAS["gap_finding"], sort_keys=True) in prompts[0]


def test_missing_gaps_preserves_validator_evidence() -> None:
    test_missing_required_gap_field_preserves_validator_evidence()


def test_prompt_contract_failure_keeps_endpoint_readiness() -> None:
    provider = _ReadyProvider()
    provider.answer = '{}'
    invalidations: list[str] = []
    worker = ManagedStageWorker(
        primary=provider,
        invalidate_readiness=lambda _p, _e, mode: invalidations.append(mode),
    )
    with pytest.raises(ProviderContractInvalidError):
        worker.run_stage("gap_finding", {"artifact": "fixture"})
    assert invalidations == []


def test_invalid_contract_invalidates_readiness() -> None:
    provider = _ReadyProvider()
    provider.native = True
    provider.answer = '{}'
    invalidations: list[str] = []
    worker = ManagedStageWorker(
        primary=provider,
        invalidate_readiness=lambda _p, _e, mode: invalidations.append(mode),
    )
    with pytest.raises(ProviderContractInvalidError):
        worker.run_stage("gap_finding", {"artifact": "fixture"})
    assert invalidations == ["native_json_schema"]


def test_valid_constrained_response_advances_once() -> None:
    provider = _ReadyProvider()
    assert ManagedStageWorker(primary=provider).run_stage("gap_finding", {"artifact": "fixture"}) == {"gaps": []}
    assert len(provider.structured_calls) == 1


def test_configured_model_must_be_live(monkeypatch: pytest.MonkeyPatch) -> None:
    import audisor.workers.local as local_module

    class Response:
        status_code = 200
        def json(self) -> dict[str, object]:
            return {"data": [{"id": "another-model"}]}
    monkeypatch.setattr(local_module, "isolated_request", lambda *a, **k: Response())
    worker = LocalWorker("http://fixture.test", "required-model")
    with pytest.raises(ProviderPermanentRequestError, match="Configured model is unavailable"):
        worker.run_live_submission_check()


def test_retry_waits_for_previous_child_reap() -> None:
    calls: list[str] = []
    class Response:
        status_code = 200
        def json(self) -> dict[str, object]:
            return {"choices": [{"text": "ready"}]}
    def request(*args: object, **kwargs: object):
        calls.append("started")
        if len(calls) == 1:
            raise IsolatedHttpTimeout("timeout after confirmed reap", child_pid=123)
        assert calls == ["started", "started"]
        return Response()
    worker = FireworksWorker("key", "http://fixture.test", "model", request=request, max_attempts=2, retry_delay_seconds=0)
    assert worker.execute(type("Task", (), {"task_id": "fixture", "prompt": "fixture"})()).answer == "ready"
    assert len(calls) == 2


def test_unreaped_child_stops_retry() -> None:
    calls = 0
    def request(*args: object, **kwargs: object):
        nonlocal calls
        calls += 1
        raise IsolatedHttpReapError("unreaped", child_pid=123)
    worker = FireworksWorker("key", "http://fixture.test", "model", request=request, max_attempts=2, retry_delay_seconds=0)
    with pytest.raises(ProviderProcessReapError):
        worker.execute(type("Task", (), {"task_id": "fixture", "prompt": "fixture"})())
    assert calls == 1


def test_production_rejects_nonterminable_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    import audisor.audisor_lifecycle.artifact_flow as flow
    from audisor.audisor_lifecycle.operation import FrozenAudisorPolicy

    provider = _ReadyProvider()
    monkeypatch.setattr(flow, "read_frozen_audisor_policy", lambda: FrozenAudisorPolicy(True, "fixture", "model", "fixture://"))
    monkeypatch.setattr(flow, "resolve_stage_providers", lambda **kwargs: (provider, None, False))
    worker, _, error = flow._resolve_worker(None, 10, state_root=Path.cwd())
    assert worker is None
    assert error is not None
    assert error["issue_code"] == "provider_capability_unsupported"
