# Legacy Audisor Runtime

This directory contains the legacy Audisor Runtime (tombstoned in 0.10.0). The
Python package is named `audisor`; the filesystem root is `openai_project` so
the separate Audisor Toolkit repository at `../audisor` remains independent.

The legacy runtime exposed these surfaces (all tombstoned in 0.10.0 except
/health and /ready):

```text
GET /health
GET /ready
POST /v1/tasks
POST /v1/builds/prepare
POST /v1/builds/{build_id}/executions
```

See [docs/provider-neutral-api.md](docs/provider-neutral-api.md) for provider
selection, readiness, errors, extension, and current limitations.

## Environment

Legacy runtime configuration used these variables (tombstoned in 0.10.0).
Never store their values in the repository.

- `AUDISOR_PROVIDER` (`fireworks` or `local-openai-compatible`; no default)
- `FIREWORKS_API_KEY`
- `FIREWORKS_BASE_URL`
- `FIREWORKS_MODEL`
- `LOCAL_MODEL_BASE_URL`
- `LOCAL_MODEL_ID`
- `LOCAL_MODEL_API_KEY`
- `AUDISOR_DATA_DIR` (optional durable build/execution root)
- `AUDISOR_ALLOWED_TARGET_ROOTS` (optional `os.pathsep`-separated target allowlist)

Selection is explicit and exclusive. Missing selection leaves readiness
degraded. Fireworks never falls back to local, and local never falls back to
Fireworks. An empty or unsupported value returns a stable provider-
configuration error before provider dispatch. The local API key is optional;
its base URL and opaque model ID are required only when local is selected.

## Dependency management

This project uses `uv` with `pyproject.toml` as the dependency declaration and
`uv.lock` as the reproducible resolved dependency set. No competing pip, Poetry,
Pipenv, or PDM manifest is used.

### Fix Engine Adapter (Optional)

The OneShot Fix engine is decoupled from the legacy Audisor Runtime. To enable Fix
capabilities natively without modifying tracked path dependencies, install
the engine interactively before running:

```powershell
uv pip install -e ../../audisor_backend
```

If the engine is not installed, the legacy runtime falls back gracefully and returns
`fix_engine_unavailable`.

## Run locally (tombstone-status verification only)

The legacy runtime execution service is deprecated and tombstoned in 0.10.0.
For tombstone-status verification only:

From `openai_project/runtime`:

```powershell
uv sync --extra dev --locked
uv run uvicorn audisor.main:app --host 127.0.0.1 --port 8000
```

Valid responses:
- `GET /health` returns `{"status":"ok"}`
- `GET /ready` reports readiness

All legacy POST endpoints return `410 legacy_runtime_deprecated`:
- `POST /v1/tasks`
- `POST /v1/builds/prepare`
- `POST /v1/builds/{build_id}/executions`

New integrations must not use those routes.

## Test

```powershell
uv run pytest
```

Optional live adapter smokes run only when every required variable for that
provider is present. Missing live configuration is reported as not run and
does not invalidate the provider-neutral API foundation.

## Builder preparation (deprecated)

The legacy runtime exposed:

    POST /v1/builds/prepare

This endpoint now returns `410 legacy_runtime_deprecated`. New integrations must not use this route.

Historically, the endpoint accepted a build ID and complete instruction, invoked the selected
worker as a planning worker, validated a strict ready-or-blocked plan, ordered
task dependencies deterministically, rendered one-time SKILL.md artifacts, and
published the complete prepared build atomically.

Prepared builds used `AUDISOR_DATA_DIR`. When it was unset, the legacy runtime selected
the platform user-data directory rather than a product-local source path:

    <data-root>/builds/<build-id>/instruction.json
    <data-root>/builds/<build-id>/plan.json
    <data-root>/builds/<build-id>/skills/<task-id>-<slug>/SKILL.md

Generated skills were build artifacts and were never installed into permanent
.agents/skills directories. A blocked plan returned HTTP 200 with specific gaps,
persisted instruction.json and plan.json, and generated no task skills.

Preparation also published `integrity.json` inside the same atomic staging
directory. It was an unsigned SHA-256 consistency anchor over the exact
instruction, plan, task records, and rendered skills. The legacy runtime rejected legacy
or altered builds without silently regenerating or repairing that anchor.

## Isolated prepared-build execution (deprecated)

The legacy runtime exposed:

    POST /v1/builds/{build_id}/executions

This endpoint now returns `410 legacy_runtime_deprecated`. New integrations must not use this route.

Historically, the endpoint bound a prepared build to an explicit target root and allowed
write paths, recorded a target baseline, copied that baseline into a per-
execution workspace, re-verified preparation integrity, and executed tasks
sequentially in deterministic dependency order. Workers continued to receive the
minimal `{task_id, prompt}` task boundary; their answer contained a strict
JSON action plan that was parsed completely before local actions began.

Only these action types were accepted:

    write_file
    create_directory
    delete_file

Filesystem effects were resolved against the isolated workspace and its allowed
paths. Expected outputs, planned and actual changed paths, write authority,
target-baseline preservation, hashes, and terminal evidence were verified
deterministically. Prepared executable validation metadata was hashed and
retained but not executed by this endpoint. Python, tests, scripts, shells, and
arbitrary commands were not run by the execution endpoint.

Durable execution data was stored beneath the prepared build:

    <data-root>/builds/<build-id>/executions/<execution-id>/
        authority.json
        baseline.json
        workspace.json
        state.json
        results/<task-id>.json
        evidence/<task-id>/
        workspace/

The real target was never used as a task write root. Failed prerequisites blocked
their dependents, interrupted running tasks were not retried, and identical
idempotent requests returned the existing durable state.

## Roadmap

The following capabilities are intentionally not part of the legacy runtime and
are reserved for future work: executable validation, real-target apply, retries,
resume, parallel execution, queues and percentage progress, A-Flow policy,
UiPath-derived orchestration, Audisor governance, evidence UI, frontend
work, deployment, and production packaging.
