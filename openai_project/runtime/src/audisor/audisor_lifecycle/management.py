"""Workspace-scoped A-Flow provider status, probes, submissions, and issues.

The configured A-Flow state root is the workspace boundary. Public status and
issue APIs expose redacted metadata only; immutable artifact content is stored
separately and is available only to the owning lifecycle/retry authority.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from audisor.config import (
    OLLAMA_BASE_URL,
    OLLAMA_MODEL_ID,
    is_aflow_enabled,
    load_provider_config,
)
from audisor.routing.configuration import build_provider_registry
from audisor.routing.router import ProviderRouter
from audisor.schemas.task_input import TaskInput
from audisor.workers.base import ProviderError, WorkerProvider
from audisor.workers.fireworks import DEFAULT_FIREWORKS_BASE_URL

from .output_processing import _parse_json_object
from .active_runs import _ACTIVE_RUN_TTL_SECONDS
from .persistence import default_state_root

PRIMARY_BUDGET_SECONDS = 120.0
FALLBACK_BUDGET_SECONDS = 150.0
RESERVED_BUDGET_SECONDS = 30.0
STAGE_BUDGET_SECONDS = 300.0
PROBE_TIMEOUT_SECONDS = 30.0

ISSUE_CODES = frozenset(
    {
        "provider_configuration_error",
        "provider_unavailable",
        "provider_timeout",
        "provider_rate_limited",
        "provider_authentication_error",
        "model_unavailable",
        "provider_invalid_response",
        "stage_schema_error",
        "persisted_state_corrupt",
        "active_run_conflict",
        "unknown",
    }
)

_SECRET_PATTERN = re.compile(
    r"(?i)(authorization|api[_-]?key|token|secret|password)\s*[:=]\s*[^\s,;]+"
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_json(path: Path, value: Mapping[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(temporary, path)
    return path


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def workspace_identity(state_root: Path | None = None) -> str:
    root = (state_root or default_state_root()).resolve()
    return hashlib.sha256(str(root).casefold().encode("utf-8")).hexdigest()[:24]


def _redact(value: object, *, limit: int = 1000) -> str:
    text = str(value).replace("\r", " ").replace("\n", " ")
    text = _SECRET_PATTERN.sub(r"\1=[redacted]", text)
    return text[:limit]


def _provider_model(provider: WorkerProvider) -> str:
    value = getattr(provider, "model_id", None) or getattr(provider, "model", None)
    return str(value or "")


def _configuration_fingerprint(provider_id: str, model: str, endpoint: str) -> str:
    body = json.dumps(
        {"provider": provider_id, "model": model, "endpoint": endpoint.rstrip("/")},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _primary_configuration() -> tuple[dict[str, str], str]:
    persisted = load_provider_config()
    if persisted is not None:
        return dict(persisted), "persisted_setup"
    return {
        "provider": "local-openai-compatible",
        "base_url": OLLAMA_BASE_URL,
        "model_id": OLLAMA_MODEL_ID,
    }, "runtime_default"


def _make_primary_provider(*, timeout_seconds: float) -> WorkerProvider:
    config, _ = _primary_configuration()
    router = ProviderRouter(config["provider"], build_provider_registry(config))
    provider = router.select_provider()
    if hasattr(provider, "timeout_seconds"):
        provider.timeout_seconds = timeout_seconds
    if hasattr(provider, "structured_output"):
        provider.structured_output = True
    return provider


def _make_fallback_provider(*, timeout_seconds: float) -> WorkerProvider:
    router = ProviderRouter("fireworks", build_provider_registry())
    provider = router.select_provider()
    if hasattr(provider, "timeout_seconds"):
        provider.timeout_seconds = timeout_seconds
    if hasattr(provider, "max_attempts"):
        provider.max_attempts = 1
    return provider


def _probe_path(root: Path) -> Path:
    return root / "management" / "provider-probes.json"


def _load_probes(root: Path) -> dict[str, Any]:
    return _read_json(_probe_path(root)) or {"providers": {}}


def _save_probe(root: Path, record: Mapping[str, Any]) -> None:
    probes = _load_probes(root)
    providers = probes.setdefault("providers", {})
    providers[str(record["provider"])] = dict(record)
    probes["updated_at"] = _utc_now()
    _atomic_json(_probe_path(root), probes)


def _run_probe(provider: WorkerProvider, *, root: Path, selection_reason: str) -> dict[str, Any]:
    provider_id = provider.provider_id
    model = _provider_model(provider)
    endpoint = str(getattr(provider, "base_url", ""))
    started = time.monotonic()
    record: dict[str, Any] = {
        "provider": provider_id,
        "model": model,
        "configuration_fingerprint": _configuration_fingerprint(provider_id, model, endpoint),
        "selection_reason": selection_reason,
        "checked_at": _utc_now(),
        "diagnostic_state": "uncertainty",
        "outcome": "unknown",
    }
    try:
        output = provider.execute(
            TaskInput(
                task_id=f"aflow-provider-probe-{provider_id}",
                prompt=(
                    "Return exactly one JSON object and no prose: "
                    '{"aflow_provider_probe":"ready"}'
                ),
            )
        )
        parsed = _parse_json_object(output.answer)
        if parsed != {"aflow_provider_probe": "ready"}:
            raise ValueError("structured probe response did not match the required object")
        record.update(diagnostic_state="valid", outcome="ready")
    except ProviderError as exc:
        record.update(
            diagnostic_state="not_valid",
            outcome=exc.code,
            detail=_redact(exc),
        )
    except Exception as exc:
        record.update(
            diagnostic_state="not_valid",
            outcome="provider_invalid_response",
            detail=_redact(f"{type(exc).__name__}: {exc}"),
        )
    record["elapsed_ms"] = round((time.monotonic() - started) * 1000)
    _save_probe(root, record)
    return record


def _probe_is_current(record: Mapping[str, Any] | None, provider: WorkerProvider) -> bool:
    if not record or record.get("outcome") != "ready":
        return False
    expected = _configuration_fingerprint(
        provider.provider_id,
        _provider_model(provider),
        str(getattr(provider, "base_url", "")),
    )
    return record.get("configuration_fingerprint") == expected


def _probe_matches_configuration(
    record: Mapping[str, Any] | None,
    *,
    provider_id: str,
    model: str,
    endpoint: str,
) -> bool:
    return bool(
        record
        and record.get("outcome") == "ready"
        and record.get("configuration_fingerprint")
        == _configuration_fingerprint(provider_id, model, endpoint)
    )


def provider_status(
    *, state_root: Path | None = None, probe: bool = False
) -> dict[str, Any]:
    """Return redacted management state; provider I/O occurs only when probe is true."""
    root = state_root or default_state_root()
    enabled = is_aflow_enabled()
    primary_config, source = _primary_configuration()
    primary_provider_id = primary_config["provider"]
    primary_model = primary_config["model_id"]
    primary_endpoint = primary_config["base_url"]
    primary_configured = all(
        value.strip()
        for value in (primary_provider_id, primary_model, primary_endpoint)
    )

    fallback_selected = os.environ.get("AUDISOR_AFLOW_FALLBACK_PROVIDER", "").strip()
    fallback_explicit = fallback_selected == "fireworks"
    fallback_fields = {
        "FIREWORKS_API_KEY": os.environ.get("FIREWORKS_API_KEY", ""),
        "FIREWORKS_MODEL": os.environ.get("FIREWORKS_MODEL", ""),
    }
    fallback_endpoint = (
        os.environ.get("FIREWORKS_BASE_URL", "").strip()
        or DEFAULT_FIREWORKS_BASE_URL
    )
    missing_fallback = [name for name, value in fallback_fields.items() if not value.strip()]
    missing_non_secret = [
        name for name in missing_fallback if name != "FIREWORKS_API_KEY"
    ]

    if probe:
        primary = _make_primary_provider(timeout_seconds=PROBE_TIMEOUT_SECONDS)
        _run_probe(primary, root=root, selection_reason="explicit_provider_probe")
        if fallback_explicit and not missing_fallback:
            fallback_probe_provider = _make_fallback_provider(
                timeout_seconds=PROBE_TIMEOUT_SECONDS
            )
            _run_probe(
                fallback_probe_provider,
                root=root,
                selection_reason="explicit_fallback_probe",
            )

    probes = _load_probes(root).get("providers", {})
    primary_probe = probes.get(primary_provider_id)
    primary_ready = _probe_matches_configuration(
        primary_probe,
        provider_id=primary_provider_id,
        model=primary_model,
        endpoint=primary_endpoint,
    )
    fallback_probe: Mapping[str, Any] | None = probes.get("fireworks")
    fallback_ready = bool(
        fallback_explicit
        and not missing_fallback
        and _probe_matches_configuration(
            fallback_probe,
            provider_id="fireworks",
            model=fallback_fields["FIREWORKS_MODEL"],
            endpoint=fallback_endpoint,
        )
    )

    active: dict[str, Any] | None = None
    artifacts_dir = root / "artifacts"
    if artifacts_dir.is_dir():
        markers = sorted(
            (
                path
                for path in artifacts_dir.glob("*.running")
                if time.time() - path.stat().st_mtime < _ACTIVE_RUN_TTL_SECONDS
            ),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if markers:
            active = _read_json(markers[0])
            if active and active.get("updated_at"):
                try:
                    updated = datetime.fromisoformat(str(active["updated_at"]))
                    live_elapsed = max(
                        0.0,
                        (datetime.now(timezone.utc) - updated).total_seconds(),
                    )
                    prior_elapsed = float(active.get("elapsed_seconds", 0.0))
                    budget = float(active.get("remaining_seconds", 0.0)) + prior_elapsed
                    active["elapsed_seconds"] = round(prior_elapsed + live_elapsed, 3)
                    active["remaining_seconds"] = round(
                        max(0.0, budget - prior_elapsed - live_elapsed), 3
                    )
                except (TypeError, ValueError):
                    pass

    return {
        "workspace_id": workspace_identity(root),
        "state_root_matches_workspace": True,
        "enabled": enabled,
        "primary": {
            "provider": primary_provider_id,
            "model": primary_model,
            "configuration_source": source,
            "configured": primary_configured,
            "missing_non_secret_fields": [
                name
                for name, value in (
                    ("LOCAL_MODEL_BASE_URL", primary_config.get("base_url", "")),
                    ("LOCAL_MODEL_ID", primary_config.get("model_id", "")),
                )
                if not value.strip()
            ],
            "endpoint_reachable": (
                primary_probe.get("diagnostic_state", "uncertainty")
                if primary_probe
                else "uncertainty"
            ),
            "model_ready": (
                primary_probe.get("diagnostic_state", "uncertainty")
                if primary_probe
                else "uncertainty"
            ),
            "structured_output_probe": (
                primary_probe.get("diagnostic_state", "uncertainty")
                if primary_probe
                else "uncertainty"
            ),
            "last_probe": primary_probe,
        },
        "fallback": {
            "provider": fallback_selected or None,
            "explicitly_configured": fallback_explicit,
            "configured": fallback_explicit and not missing_fallback,
            "credential_configured": bool(fallback_fields["FIREWORKS_API_KEY"].strip()),
            "missing_non_secret_fields": missing_non_secret,
            "structured_output_probe": (
                fallback_probe.get("diagnostic_state", "uncertainty")
                if fallback_probe
                else "uncertainty"
            ),
            "ready": fallback_ready,
            "last_probe": fallback_probe,
        },
        "current_run": active,
        "budgets_seconds": {
            "primary": PRIMARY_BUDGET_SECONDS,
            "fallback": FALLBACK_BUDGET_SECONDS,
            "reserved": RESERVED_BUDGET_SECONDS,
            "stage_total": STAGE_BUDGET_SECONDS,
        },
        "can_submit": bool(enabled and primary_configured and active is None),
    }


def resolve_stage_providers(
    *, state_root: Path | None = None
) -> tuple[WorkerProvider, WorkerProvider | None, bool]:
    """Resolve primary/fallback through ProviderRouter and ProviderRegistry."""
    root = state_root or default_state_root()
    primary = _make_primary_provider(timeout_seconds=PRIMARY_BUDGET_SECONDS)
    fallback: WorkerProvider | None = None
    ready = False
    selected = os.environ.get("AUDISOR_AFLOW_FALLBACK_PROVIDER", "").strip()
    required = (
        os.environ.get("FIREWORKS_API_KEY", "").strip(),
        os.environ.get("FIREWORKS_MODEL", "").strip(),
    )
    if selected == "fireworks" and all(required):
        fallback = _make_fallback_provider(timeout_seconds=FALLBACK_BUDGET_SECONDS)
        probe_record = _load_probes(root).get("providers", {}).get("fireworks")
        ready = _probe_is_current(probe_record, fallback)
    return primary, fallback, ready


def persist_submission_snapshot(
    root: Path,
    trigger: Mapping[str, Any],
    *,
    digest: str,
    run_id: str,
    revision: int | None,
) -> Path:
    """Persist the immutable six-field trigger separately from public issue data."""
    snapshot = {
        "artifact_id": trigger["artifact_id"],
        "artifact_type": trigger["artifact_type"],
        "status": trigger["status"],
        "content": trigger["content"],
        "intent": trigger["intent"],
        "context": trigger["context"],
        "submission_digest": digest,
        "lifecycle_run_id": run_id,
        "artifact_revision": revision,
        "captured_at": _utc_now(),
    }
    path = root / "submissions" / f"{digest}-{run_id}.json"
    return _atomic_json(path, snapshot)


def persist_run_evidence(
    root: Path,
    result: Mapping[str, Any],
    provider_attempts: list[Mapping[str, Any]],
) -> Path | None:
    run_id = result.get("lifecycle_run_id")
    if not isinstance(run_id, str) or not run_id:
        return None
    record = {
        "workspace_id": workspace_identity(root),
        "artifact_id": result.get("artifact_id"),
        "artifact_revision": result.get("artifact_revision"),
        "lifecycle_run_id": run_id,
        "submission_digest": result.get("submission_digest"),
        "status": result.get("status"),
        "stage": result.get("stage"),
        "started_at": result.get("started_at"),
        "completed_at": result.get("completed_at"),
        "provider_attempts": [dict(item) for item in provider_attempts],
    }
    try:
        return _atomic_json(root / "runs" / f"{run_id}.json", record)
    except OSError:
        return None


def _classify(result: Mapping[str, Any]) -> str:
    explicit = result.get("issue_code")
    if explicit in ISSUE_CODES:
        return str(explicit)
    stage = result.get("stage")
    detail = str(result.get("detail", "")).casefold()
    internal = str(result.get("provider_error_detail", "")).casefold()
    if stage == "active_run":
        return "active_run_conflict"
    if stage == "persisted_state":
        return "persisted_state_corrupt"
    if "schema" in detail or "not an object" in detail:
        return "stage_schema_error"
    if "timeout" in detail or "timed out" in detail:
        return "provider_timeout"
    if "http_status=404" in internal:
        return "model_unavailable"
    return "unknown"


def _direct_cause(code: str) -> str:
    return {
        "provider_configuration_error": "The selected provider configuration is incomplete.",
        "provider_unavailable": "The selected provider could not be reached.",
        "provider_timeout": "The selected provider did not return before its request budget expired.",
        "provider_rate_limited": "The selected provider rejected the attempt because of rate limiting.",
        "provider_authentication_error": "The selected provider rejected authentication.",
        "model_unavailable": "The configured model is not available from the selected provider.",
        "provider_invalid_response": "The provider response did not satisfy the A-Flow stage contract.",
        "stage_schema_error": "The stage output did not satisfy its strict schema.",
        "persisted_state_corrupt": "The newest persisted lifecycle state is corrupt or incomplete.",
        "active_run_conflict": "An identical lifecycle is already active.",
        "unknown": "The lifecycle ended because of an unclassified internal error.",
    }[code]


def _resolution(code: str, fallback_ready: bool) -> tuple[list[str], list[str], str]:
    if code == "provider_timeout":
        steps = [
            "Check the configured local endpoint and model.",
            "Run the structured provider probe.",
        ]
        if not fallback_ready:
            steps.append("Complete explicit Fireworks base URL and model configuration, then probe again.")
        steps.append("Start a linked retry from the owning OperationController operation, or resubmit from the originating MCP client.")
        return steps, ["Provider probe is valid", "Owning retry authority is available"], "The retried stage returns a schema-valid result within its provider budget."
    if code == "provider_configuration_error":
        return ["Complete the named non-secret provider fields.", "Run the structured provider probe.", "Retry through the owning authority."], ["Configuration is complete"], "Provider status reports configured and the structured probe is valid."
    if code == "persisted_state_corrupt":
        return ["Inspect the bounded state-path evidence.", "Recover or remove only the corrupt record after explicit authorization.", "Resubmit the immutable artifact."], ["State recovery is authorized", "A rollback copy exists"], "The newest state record is readable and the resubmission starts normally."
    if code == "active_run_conflict":
        return ["Wait for the active lifecycle to finish.", "Reattach with aflow_last_result."], ["The active run is terminal"], "The persisted result is returned without a second run."
    return ["Review the redacted evidence and provider attempts.", "Run the next permitted provider probe.", "Retry only through the owning execution authority."], ["The cause is distinguished by new evidence"], "The lifecycle advances without the same classified error."


def _operation_id(trigger: Mapping[str, Any]) -> str | None:
    context = trigger.get("context")
    if not isinstance(context, str):
        return None
    try:
        parsed = json.loads(context)
    except json.JSONDecodeError:
        return None
    value = parsed.get("operation_id") if isinstance(parsed, dict) else None
    return value if isinstance(value, str) and value.strip() else None


def create_root_cause_issue(
    root: Path,
    result: Mapping[str, Any],
    trigger: Mapping[str, Any],
    provider_attempts: list[Mapping[str, Any]],
) -> dict[str, Any]:
    code = _classify(result)
    run_id = str(result.get("lifecycle_run_id") or "unknown")
    issue_id = "aflow-" + hashlib.sha256(
        f"{run_id}:{result.get('stage')}:{code}".encode("utf-8")
    ).hexdigest()[:20]
    try:
        readiness = provider_status(state_root=root, probe=False)
    except Exception as exc:
        readiness = {
            "enabled": False,
            "primary": {
                "configured": False,
                "endpoint_reachable": "uncertainty",
                "model_ready": "uncertainty",
                "structured_output_probe": "uncertainty",
            },
            "fallback": {
                "ready": False,
                "missing_non_secret_fields": [],
                "structured_output_probe": "uncertainty",
            },
            "management_error": _redact(f"{type(exc).__name__}: {exc}"),
        }
    fallback_ready = readiness["fallback"]["ready"]
    steps, prerequisites, expected = _resolution(code, bool(fallback_ready))
    operation_id = _operation_id(trigger)
    known_underlying = code in {"provider_configuration_error", "persisted_state_corrupt", "active_run_conflict"}
    underlying = _direct_cause(code) if known_underlying else "I don't know."
    issue: dict[str, Any] = {
        "issue_id": issue_id,
        "workspace_id": workspace_identity(root),
        "artifact_id": result.get("artifact_id") or trigger.get("artifact_id"),
        "artifact_revision": result.get("artifact_revision"),
        "lifecycle_run_id": result.get("lifecycle_run_id"),
        "submission_digest": result.get("submission_digest"),
        "operation_id": operation_id,
        "created_at": result.get("completed_at") or _utc_now(),
        "issue_code": code,
        "stage": result.get("stage", "unknown"),
        "diagnostic_state": "valid" if code != "unknown" else "uncertainty",
        "explanation": {
            "symptom": _redact(result.get("detail", "A-Flow lifecycle ended with an error.")),
            "direct_cause": _direct_cause(code),
            "underlying_cause": underlying,
            "impact": "The current A-Flow lifecycle action is blocked.",
            "blocked_action": str(result.get("stage", "lifecycle transition")),
            "checked": ["normalized provider outcome", "stage timing", "provider readiness", "persisted lifecycle identity"],
            "remains_unknown": [] if known_underlying else ["provider-side model performance", "request complexity", "local resource pressure"],
            "evidence_required": [] if known_underlying else ["a successful or differently classified structured probe", "provider-side diagnostics for the same time window"],
            "next_diagnostic_action": "Run the explicit provider probe and inspect the redacted attempt record.",
        },
        "evidence": {
            "provider_attempts": [dict(item) for item in provider_attempts],
            "bounded_error_detail": _redact(result.get("detail", "")),
            "provider_readiness": {
                "enabled": readiness.get("enabled"),
                "primary": {
                    key: readiness.get("primary", {}).get(key)
                    for key in (
                        "configured",
                        "endpoint_reachable",
                        "model_ready",
                        "structured_output_probe",
                    )
                },
                "fallback": {
                    key: readiness.get("fallback", {}).get(key)
                    for key in (
                        "ready",
                        "missing_non_secret_fields",
                        "structured_output_probe",
                    )
                },
                "management_error": readiness.get("management_error"),
            },
        },
        "resolution": {
            "steps": steps,
            "prerequisites": prerequisites,
            "expected_successful_resolution": expected,
            "automatic_actions_attempted": [
                f"{item.get('provider')}:{item.get('outcome')}" for item in provider_attempts
            ],
        },
        "retry": {
            "supported": operation_id is not None,
            "owning_execution_authority": "operation_controller" if operation_id else "originating_mcp_client",
            "permitted_provider": "local-openai-compatible",
            "linked_retry_run": None,
            "instruction": (
                "Use the linked operation recovery action."
                if operation_id
                else "Resubmit the immutable six-field artifact from the originating MCP client."
            ),
        },
    }
    path = root / "issues" / f"{issue_id}.json"
    if path.exists():
        return _read_json(path) or issue
    _atomic_json(path, issue)
    return issue


def list_issues(
    *, state_root: Path | None = None, cursor: str | None = None, limit: int = 50
) -> dict[str, Any]:
    root = state_root or default_state_root()
    bounded_limit = max(1, min(int(limit), 100))
    try:
        offset = max(0, int(cursor or "0"))
    except ValueError:
        offset = 0
    issue_dir = root / "issues"
    records: list[dict[str, Any]] = []
    if issue_dir.is_dir():
        for path in issue_dir.glob("*.json"):
            value = _read_json(path)
            if value and value.get("workspace_id") == workspace_identity(root):
                records.append(get_issue(str(value.get("issue_id")), state_root=root) or value)
    records.sort(key=lambda item: str(item.get("created_at", "")), reverse=True)
    page = records[offset : offset + bounded_limit]
    items = [
        {
            key: item.get(key)
            for key in (
                "issue_id",
                "workspace_id",
                "artifact_id",
                "artifact_revision",
                "lifecycle_run_id",
                "submission_digest",
                "operation_id",
                "created_at",
                "issue_code",
                "stage",
                "diagnostic_state",
            )
        }
        | {"retry": item.get("retry", {})}
        for item in page
    ]
    next_offset = offset + len(page)
    return {
        "workspace_id": workspace_identity(root),
        "items": items,
        "next_cursor": str(next_offset) if next_offset < len(records) else None,
        "total": len(records),
    }


def initialize_management_state(*, state_root: Path | None = None) -> int:
    """Add issue records for legacy persisted lifecycle errors, once per run.

    Existing artifact results are read-only. The additive issue is deliberately
    marked as an external-client retry because the original six-field snapshot
    may predate snapshot persistence.
    """
    root = state_root or default_state_root()
    known_runs = {
        item.get("lifecycle_run_id")
        for item in list_issues(state_root=root, limit=100)["items"]
    }
    artifacts = root / "artifacts"
    if not artifacts.is_dir():
        return 0
    created = 0
    for path in sorted(artifacts.glob("*.json")):
        result = _read_json(path)
        if not result or result.get("status") != "error":
            continue
        run_id = result.get("lifecycle_run_id")
        if run_id in known_runs:
            continue
        artifact_id = result.get("artifact_id")
        if not isinstance(artifact_id, str) or not artifact_id:
            continue
        trigger = {
            "artifact_id": artifact_id,
            "artifact_type": "unknown",
            "status": "draft_complete",
            "content": "",
            "intent": "Legacy persisted lifecycle result",
            "context": "Original immutable submission snapshot predates A-Flow management.",
        }
        create_root_cause_issue(root, result, trigger, [])
        known_runs.add(run_id)
        created += 1
    return created


def get_issue(issue_id: str, *, state_root: Path | None = None) -> dict[str, Any] | None:
    if not re.fullmatch(r"aflow-[0-9a-f]{20}", issue_id):
        return None
    root = state_root or default_state_root()
    issue = _read_json(root / "issues" / f"{issue_id}.json")
    if issue is None or issue.get("workspace_id") != workspace_identity(root):
        return None
    retries_dir = root / "issue-retries"
    history: list[dict[str, Any]] = []
    if retries_dir.is_dir():
        for path in sorted(retries_dir.glob(f"{issue_id}-*.json")):
            record = _read_json(path)
            if record:
                history.append(record)
    if history:
        issue = dict(issue)
        issue["retry"] = dict(issue.get("retry", {}))
        issue["retry"]["linked_retry_run"] = history[-1].get("lifecycle_run_id")
        issue["retry"]["permitted_provider"] = history[-1].get("provider")
        issue["retry_history"] = history
    return issue


def link_issue_retry(
    issue_id: str,
    *,
    lifecycle_run_id: str | None,
    provider: str,
    state_root: Path | None = None,
) -> dict[str, Any] | None:
    """Append retry linkage to the issue without rewriting issue history."""
    root = state_root or default_state_root()
    issue = get_issue(issue_id, state_root=root)
    if issue is None:
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    record = {
        "issue_id": issue_id,
        "lifecycle_run_id": lifecycle_run_id,
        "provider": provider,
        "created_at": _utc_now(),
    }
    _atomic_json(root / "issue-retries" / f"{issue_id}-{stamp}.json", record)
    return get_issue(issue_id, state_root=root)


def update_active_progress(
    marker_path: Path | None,
    *,
    stage: str,
    provider: str,
    attempt: int,
    elapsed_seconds: float,
    remaining_seconds: float,
) -> None:
    if marker_path is None:
        return
    current = _read_json(marker_path) or {}
    current.update(
        {
            "stage": stage,
            "provider": provider,
            "provider_attempt": attempt,
            "elapsed_seconds": round(elapsed_seconds, 3),
            "remaining_seconds": max(0.0, round(remaining_seconds, 3)),
            "updated_at": _utc_now(),
        }
    )
    try:
        _atomic_json(marker_path, current)
    except OSError:
        return
