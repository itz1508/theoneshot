"""Canonical A-Flow artifact lifecycle engine.

Runtime code owns the deterministic state machine. The stage worker reasons
only inside stages; stage ordering and the unresolved-gap barrier are
enforced here, in code. A handoff package is advisory: it is execution-ready
because every gap was fixed, evaluation passed, and success criteria plus
fixture cases exist.

Result statuses are exactly ``improved | unresolved_gap | skip | error``.

This module orchestrates the fixed stage sequence; each capability lives in
its own module (contracts, prompts, worker, output processing, identity,
persistence, active runs, stage execution, evaluation repair, result
building). Every name the engine has ever exposed from here remains
importable from this module — the compatibility manifest asserts it.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from audisor.workers.base import (
    ProviderAuthenticationError,
    ProviderContractInvalidError,
    ProviderError,
    ProviderPermanentRequestError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)

from .operation import FrozenAudisorPolicy, read_frozen_audisor_policy

# Compatibility surface: every name previously defined in this module stays
# importable from it (public contract plus the underscore names the manifest
# builder and the test suite reach for).
from .active_runs import (
    _ACTIVE_RUN_TTL_SECONDS,
    _active_run_marker_path,
    _guard_active_run,
)
from .evaluation_repair import (
    _evaluation_findings_as_unresolved,
    _normalize_unresolved,
    _refix_contract,
    _run_repair_cycle,
    _unresolved_result,
)
from .identity import _canonical_content, _content_digest, _submission_digest
from .output_processing import (
    _drop_empty_optionals,
    _parse_json_object,
    _prune_to_schema,
)
from .persistence import (
    PersistedResultError,
    _persist_result,
    _sanitize_artifact_id,
    default_state_root,
    read_last_result,
)
from .result_builder import _LifecycleRunState
from .management import (
    STAGE_BUDGET_SECONDS,
    authorize_submission,
    create_root_cause_issue,
    invalidate_provider_readiness,
    persist_submission_snapshot,
    resolve_stage_providers,
    update_active_progress,
)
from .stage_contracts import (
    REPAIR_POLICY,
    RESULT_STATUSES,
    STAGE_OUTPUT_SCHEMAS,
    STAGES,
    StageOutputError,
    StageWorker,
    _EVIDENCE_BASIS,
    _MAX_REPAIRED_FIELDS,
    _OPTIONAL_STRING_FIELDS,
    _REPAIRABLE_STAGES,
    _STAGE_VALIDATORS,
    _UNRESOLVED_GAP_ITEM,
)
from .stage_execution import _call_with_timeout, _execute_stage
from .stage_prompts import _STAGE_INSTRUCTIONS, _STAGE_OUTPUT_EXAMPLES
from .stage_worker import LocalStageWorker, ManagedStageWorker


def _resolve_prior_state(
    artifact_id: str,
    digest: str,
    content_digest: str,
    state_root: Path | None,
) -> tuple[dict[str, Any] | None, int | None]:
    """Replay and revision resolution against the newest persisted result.

    Returns ``(early_result, revision)``: a persisted-state error or a
    replayed prior outcome ends the run before any stage executes; otherwise
    the runtime-derived revision for this submission is returned.
    """
    try:
        previous = read_last_result(artifact_id, state_root=state_root)
    except PersistedResultError as exc:
        # Corrupt or incomplete persisted state: bounded structured error,
        # never silently treated as a completed outcome. Deliberately not
        # persisted — a persisted error would bury the corrupt file and
        # mask the corruption on the next read.
        return (
            {
                "status": "error",
                "stage": "persisted_state",
                "detail": str(exc),
                "artifact_id": artifact_id,
                "state_path": exc.state_path,
                "failure": exc.failure,
            },
            None,
        )
    if (
        previous is not None
        and previous.get("submission_digest") == digest
        and previous.get("status") in ("improved", "unresolved_gap")
    ):
        # Observable replay: the returned view says so, while the persisted
        # record keeps its original "started" disposition.
        replayed = dict(previous)
        replayed["submission_disposition"] = "replayed"
        return replayed, None

    # Runtime-derived revision sequence: unchanged canonical content keeps
    # its revision; changed content advances it.
    prev_revision = previous.get("artifact_revision") if previous is not None else None
    if isinstance(prev_revision, int) and prev_revision >= 1:
        revision = (
            prev_revision
            if previous.get("content_digest") == content_digest
            else prev_revision + 1
        )
    else:
        revision = 1
    return None, revision


def _resolve_worker(
    worker: StageWorker | None,
    stage_timeout_seconds: float | None,
    *,
    state_root: Path | None = None,
    progress=None,
) -> tuple[StageWorker | None, float | None, dict[str, Any] | None]:
    """Fill worker and timeout from the frozen policy when not supplied."""
    if stage_timeout_seconds is None or worker is None:
        policy = read_frozen_audisor_policy()
        if stage_timeout_seconds is None:
            stage_timeout_seconds = STAGE_BUDGET_SECONDS
        if worker is None:
            if not policy.enabled:
                return (
                    None,
                    stage_timeout_seconds,
                    {
                        "status": "error",
                        "stage": "configuration",
                        "issue_code": "provider_configuration_error",
                        "detail": "A-Flow is disabled by configuration",
                    },
                )
            try:
                primary, fallback, fallback_ready = resolve_stage_providers(
                    state_root=state_root
                )
            except Exception as exc:
                return (
                    None,
                    stage_timeout_seconds,
                    {
                        "status": "error",
                        "stage": "configuration",
                        "issue_code": getattr(exc, "code", "provider_configuration_error"),
                        "detail": str(exc),
                    },
                )
            if not primary.capabilities().terminable_execution:
                return (
                    None,
                    stage_timeout_seconds,
                    {
                        "status": "error",
                        "stage": "configuration",
                        "issue_code": "provider_capability_unsupported",
                        "detail": "Production A-Flow provider cannot guarantee terminable execution",
                    },
                )

            def invalidate(provider, error: ProviderError, schema_mode: str) -> None:
                endpoint_failure = isinstance(
                    error,
                    (
                        ProviderUnavailableError,
                        ProviderTimeoutError,
                        ProviderAuthenticationError,
                    ),
                ) or (
                    isinstance(error, ProviderPermanentRequestError)
                    and "model=unavailable" in error.internal_detail
                )
                native_contract_failure = (
                    isinstance(error, ProviderContractInvalidError)
                    and schema_mode == "native_json_schema"
                )
                if endpoint_failure or native_contract_failure:
                    invalidate_provider_readiness(
                        provider,
                        root=state_root or default_state_root(),
                        reason=error.code,
                        schema_only=native_contract_failure and not endpoint_failure,
                    )
            worker = ManagedStageWorker(
                primary=primary,
                fallback=fallback,
                fallback_ready=fallback_ready,
                progress=progress,
                invalidate_readiness=invalidate,
            )
    return worker, stage_timeout_seconds, None


def _run_stage_pipeline(
    run_stage,
    common: Mapping[str, Any],
    content: str,
    state: _LifecycleRunState,
) -> dict[str, Any]:
    """Fixed stage sequence: gap_finding -> gap_fixing -> unresolved-gap
    barrier -> evaluation (with at most one bounded repair cycle) ->
    success_criteria -> fixture_design -> handoff."""
    # Stage 1: gap_finding
    found = run_stage("gap_finding", {**common, "artifact": content})
    if found.get("status") == "error":
        return dict(found)
    gap_findings = [dict(gap) for gap in found["gaps"]]

    # Stage 2: gap_fixing
    fixed = run_stage("gap_fixing", {**common, "artifact": content, "gaps": gap_findings})
    if fixed.get("status") == "error":
        return dict(fixed)
    gap_fixes = [dict(fix) for fix in fixed["fixes"]]
    improved_artifact = fixed["improved_artifact"]

    # Unresolved-gap barrier: pure code, not model. Nothing downstream runs
    # while any gap remains unresolved.
    if fixed["unresolved"]:
        return _unresolved_result(
            content, gap_findings, gap_fixes, _normalize_unresolved(fixed["unresolved"])
        )

    # Stage 3: evaluation (only reachable when the unresolved list is empty)
    evaluated = run_stage(
        "evaluation",
        {**common, "improved_artifact": improved_artifact, "gap_fixes": gap_fixes},
    )
    if evaluated.get("status") == "error":
        return dict(evaluated)
    if not evaluated["passed"]:
        early, improved_artifact, evaluated = _run_repair_cycle(
            run_stage,
            common,
            content,
            gap_findings,
            gap_fixes,
            improved_artifact,
            evaluated,
            state,
        )
        if early is not None:
            return early
    evaluation = {
        "passed": True,
        "summary": evaluated["summary"],
        "findings": [dict(finding) for finding in evaluated["findings"]],
    }
    return _run_handoff_stages(
        run_stage, common, content, gap_findings, gap_fixes, improved_artifact, evaluation
    )


def _run_handoff_stages(
    run_stage,
    common: Mapping[str, Any],
    content: str,
    gap_findings: list[dict[str, Any]],
    gap_fixes: list[dict[str, Any]],
    improved_artifact: str,
    evaluation: dict[str, Any],
) -> dict[str, Any]:
    """Post-evaluation stages: success_criteria, fixture_design, handoff."""
    # Stage 4: success_criteria (derived only after evaluation passes)
    criteria_output = run_stage(
        "success_criteria",
        {**common, "improved_artifact": improved_artifact, "evaluation": evaluation},
    )
    if criteria_output.get("status") == "error":
        return dict(criteria_output)
    success_criteria = [dict(criterion) for criterion in criteria_output["criteria"]]

    # Stage 5: fixture_design (post-build validation cases)
    fixtures_output = run_stage(
        "fixture_design",
        {**common, "improved_artifact": improved_artifact, "success_criteria": success_criteria},
    )
    if fixtures_output.get("status") == "error":
        return dict(fixtures_output)
    fixture_cases = [dict(case) for case in fixtures_output["fixture_cases"]]

    # Handoff: advisory, execution-ready because the work above proved it.
    return {
        "status": "improved",
        "original_artifact": content,
        "gap_findings": gap_findings,
        "gap_fixes": gap_fixes,
        "improved_artifact": improved_artifact,
        "evaluation": evaluation,
        "success_criteria": success_criteria,
        "fixture_cases": fixture_cases,
    }


def run_artifact_lifecycle(
    trigger: Mapping[str, Any],
    worker: StageWorker | None = None,
    *,
    stage_timeout_seconds: float | None = None,
    state_root: Path | None = None,
) -> dict[str, Any]:
    """Run one artifact cycle. Stage ordering is fixed here, never by the worker.

    Sequence: gap_finding -> gap_fixing -> unresolved-gap barrier ->
    evaluation -> (at most one bounded gap_fixing/evaluation repair cycle) ->
    success_criteria -> fixture_design -> handoff. Identity is runtime-derived:
    a canonical content digest drives the revision sequence, the submission
    digest (canonical content + artifact_type + intent + context) drives
    idempotent replay, and every run gets a lifecycle_run_id and timestamps.
    An unchanged resubmission replays the persisted review outcome; an
    identical submission against a still-active run returns that run's
    identity; corrupt persisted state is a bounded error.
    """
    if not isinstance(trigger, Mapping):
        return {"status": "skip", "reason": "trigger must be a JSON object"}

    artifact_id = trigger.get("artifact_id")
    valid_id = isinstance(artifact_id, str) and bool(artifact_id.strip())
    root = state_root or default_state_root()
    state = _LifecycleRunState(
        root=root,
        artifact_id=artifact_id,
        valid_id=valid_id,
        trigger=trigger,
    )

    status = trigger.get("status")
    if status != "draft_complete":
        return state.finish(
            {"status": "skip", "reason": f"status is {status!r}; lifecycle runs only on 'draft_complete'"}
        )
    content = trigger.get("content")
    if not isinstance(content, str) or not content.strip():
        return state.finish({"status": "skip", "reason": "content is empty; nothing to review"})
    if not valid_id:
        return state.finish({"status": "skip", "reason": "artifact_id must be a non-empty string"})

    # Idempotency: digests are runtime-derived, never agent-supplied. An
    # unchanged resubmission replays the persisted review outcome; errors and
    # skips are never replayed, so transient failures stay retryable.
    state.digest = _submission_digest(content, trigger)
    state.content_digest = _content_digest(content)
    state.started_at = datetime.now(timezone.utc).isoformat()
    state.run_id = uuid.uuid4().hex
    early, revision = _resolve_prior_state(
        artifact_id, state.digest, state.content_digest, state_root
    )
    if early is not None:
        if early.get("status") == "error":
            create_root_cause_issue(root, early, trigger, [])
        return early
    state.revision = revision

    # Admission precedes immutable submission persistence and active-run
    # creation. Production providers must prove current readiness using the
    # exact provider instance that will execute the stages.
    worker, stage_timeout_seconds, config_error = _resolve_worker(
        worker,
        stage_timeout_seconds,
        state_root=root,
        progress=None,
    )
    if config_error is not None:
        return state.finish(config_error)
    if isinstance(worker, ManagedStageWorker):
        admission = authorize_submission(worker.primary, state_root=root)
        if admission.get("outcome") != "ready":
            return state.finish(
                {
                    "status": "error",
                    "stage": "provider_readiness",
                    "issue_code": admission.get("outcome", "provider_unavailable"),
                    "detail": admission.get("detail", "Provider readiness did not authorize submission"),
                }
            )
        state.provider_attempts = worker.attempts

    try:
        persist_submission_snapshot(
            root,
            trigger,
            digest=state.digest,
            run_id=state.run_id,
            revision=state.revision,
        )
    except OSError as exc:
        return state.finish(
            {
                "status": "error",
                "stage": "configuration",
                "issue_code": "persisted_state_corrupt",
                "detail": f"immutable submission snapshot could not be persisted: {exc}",
            }
        )

    early, state.marker_path = _guard_active_run(
        root, artifact_id, state.digest, state.run_id, state.started_at
    )
    if early is not None:
        create_root_cause_issue(root, early, trigger, [])
        return early

    def progress(stage: str, provider: str, attempt: int, elapsed: float, remaining: float) -> None:
        update_active_progress(
            state.marker_path,
            stage=stage,
            provider=provider,
            attempt=attempt,
            elapsed_seconds=elapsed,
            remaining_seconds=remaining,
        )

    if isinstance(worker, ManagedStageWorker):
        worker.progress = progress

    common = {
        "artifact_id": artifact_id,
        "artifact_type": trigger.get("artifact_type"),
        "intent": trigger.get("intent"),
        "context": trigger.get("context"),
    }

    def run_stage(stage: str, payload: Mapping[str, Any]) -> Mapping[str, Any] | dict[str, Any]:
        return _execute_stage(worker, stage, payload, stage_timeout_seconds, state.repairs)

    return state.finish(_run_stage_pipeline(run_stage, common, content, state))
