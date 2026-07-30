from __future__ import annotations

import json
from pathlib import Path

import pytest

from audisor.audisor_lifecycle.management import (
    create_root_cause_issue,
    get_issue,
    initialize_management_state,
    list_issues,
    persist_submission_snapshot,
    provider_status,
    resolve_stage_providers,
)
from audisor.audisor_lifecycle.stage_worker import ManagedStageWorker
from audisor.schemas.task_output import TaskOutput
from audisor.workers.base import (
    ProviderAuthenticationError,
    ProviderCapabilities,
    ProviderTimeoutError,
)
from audisor.workers.fireworks import FireworksWorker


class FakeProvider:
    def __init__(self, provider_id: str, *, answer: str = '{"gaps": []}', error=None):
        self.provider_id = provider_id
        self.model_id = "fixture-model"
        self.timeout_seconds = 1.0
        self.answer = answer
        self.error = error
        self.calls = 0

    def configuration_status(self) -> bool:
        return True

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(text=True)

    def execute(self, task):
        self.calls += 1
        if self.error:
            raise self.error
        return TaskOutput(task_id=task.task_id, answer=self.answer)


def test_blank_fallback_endpoint_means_unconfigured_and_blocks_probe_gate(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("AUDISOR_AFLOW_FALLBACK_PROVIDER", "fireworks")
    monkeypatch.setenv("FIREWORKS_API_KEY", "fixture-key")
    monkeypatch.setenv("FIREWORKS_MODEL", "accounts/example/models/fixture")
    monkeypatch.setenv("FIREWORKS_BASE_URL", "")

    status = provider_status(state_root=tmp_path, probe=False)
    assert status["fallback"]["configured"] is False
    assert "FIREWORKS_BASE_URL" in status["fallback"]["missing_non_secret_fields"]
    assert status["fallback"]["ready"] is False


def test_transient_primary_error_uses_exactly_one_nonoverlapping_fallback() -> None:
    primary = FakeProvider(
        "local-openai-compatible",
        error=ProviderTimeoutError("Selected provider request timed out"),
    )
    fallback = FakeProvider("fireworks")
    worker = ManagedStageWorker(primary=primary, fallback=fallback, fallback_ready=True)

    assert worker.run_stage("gap_finding", {"artifact": "x"}) == {"gaps": []}
    assert primary.calls == 1
    assert fallback.calls == 1
    assert [attempt["fallback_usage"] for attempt in worker.attempts] == [False, True]


def test_authentication_error_never_uses_fallback() -> None:
    primary = FakeProvider(
        "local-openai-compatible",
        error=ProviderAuthenticationError("Selected provider rejected authentication"),
    )
    fallback = FakeProvider("fireworks")
    worker = ManagedStageWorker(primary=primary, fallback=fallback, fallback_ready=True)

    with pytest.raises(ProviderAuthenticationError):
        worker.run_stage("gap_finding", {"artifact": "x"})
    assert primary.calls == 1
    assert fallback.calls == 0


def test_issue_is_append_only_redacted_and_list_omits_artifact_content(tmp_path: Path) -> None:
    trigger = {
        "artifact_id": "artifact.management",
        "artifact_type": "plan",
        "status": "draft_complete",
        "content": "secret artifact body",
        "intent": "test issue persistence",
        "context": "external MCP",
    }
    persist_submission_snapshot(
        tmp_path,
        trigger,
        digest="a" * 64,
        run_id="run-1",
        revision=1,
    )
    result = {
        "status": "error",
        "stage": "gap_finding",
        "detail": "Provider timed out; Authorization=Bearer-never-store",
        "issue_code": "provider_timeout",
        "artifact_id": trigger["artifact_id"],
        "artifact_revision": 1,
        "lifecycle_run_id": "run-1",
        "submission_digest": "a" * 64,
        "completed_at": "2026-07-29T00:00:00+00:00",
    }
    issue = create_root_cause_issue(tmp_path, result, trigger, [])
    repeated = create_root_cause_issue(tmp_path, result, trigger, [])

    assert repeated == issue
    assert issue["explanation"]["underlying_cause"] == "I don't know."
    assert "Bearer-never-store" not in json.dumps(issue)
    page = list_issues(state_root=tmp_path)
    assert page["total"] == 1
    assert "content" not in json.dumps(page)
    assert get_issue(issue["issue_id"], state_root=tmp_path) == issue
    snapshot = next((tmp_path / "submissions").glob("*.json"))
    assert json.loads(snapshot.read_text(encoding="utf-8"))["content"] == "secret artifact body"


def test_unresolved_gap_is_not_an_operational_issue(tmp_path: Path) -> None:
    assert list_issues(state_root=tmp_path)["items"] == []


def test_legacy_error_import_is_additive_and_idempotent(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    original = {
        "status": "error",
        "stage": "gap_finding",
        "detail": "ProviderTimeoutError: request timed out after 300 seconds",
        "artifact_id": "artifact.revision-3",
        "artifact_revision": 3,
        "lifecycle_run_id": "run-revision-3",
        "submission_digest": "c" * 64,
        "completed_at": "2026-07-29T00:00:00+00:00",
    }
    path = artifacts / "artifact.revision-3-20260729.json"
    path.write_text(json.dumps(original), encoding="utf-8")

    assert initialize_management_state(state_root=tmp_path) == 1
    assert initialize_management_state(state_root=tmp_path) == 0
    assert json.loads(path.read_text(encoding="utf-8")) == original
    issue = get_issue(list_issues(state_root=tmp_path)["items"][0]["issue_id"], state_root=tmp_path)
    assert issue is not None
    assert issue["issue_code"] == "provider_timeout"
    assert "300 seconds" in issue["explanation"]["symptom"]
