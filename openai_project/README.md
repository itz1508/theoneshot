# Audisor Runtime

This directory is the canonical OpenAI project for the Audisor runtime. The
Python package is named `audisor`; the filesystem root is `openai_project` so
the separate Audisor Toolkit repository at `../audisor` remains independent.

The local API is provider-neutral and exposes these stable surfaces:

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

Runtime configuration uses these variables. Never store their values in the
repository.

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

The canonical Fix engine is decoupled from the base runtime. To enable Fix
capabilities natively without modifying tracked path dependencies, install
the engine interactively before running:

```powershell
uv pip install -e ../../audisor_backend
```

If the engine is not installed, the runtime falls back gracefully and returns
`fix_engine_unavailable`.

## Run locally

From `openai_project/runtime`:

```powershell
uv sync --extra dev --locked
uv run uvicorn audisor.main:app --host 127.0.0.1 --port 8000
```

Example request:

```json
[
  {
    "task_id": "task-001",
    "prompt": "Return the word ready."
  }
]
```

Response shape:

```json
[
  {
    "task_id": "task-001",
    "answer": "ready"
  }
]
```

## Test

```powershell
uv run pytest
```

Optional live adapter smokes run only when every required variable for that
provider is present. Missing live configuration is reported as not run and
does not invalidate the provider-neutral API foundation.

## Builder preparation

The runtime exposes:

    POST /v1/builds/prepare

The endpoint accepts a build ID and complete instruction, invokes the selected
worker as a planning worker, validates a strict ready-or-blocked plan, orders
task dependencies deterministically, renders one-time SKILL.md artifacts, and
publishes the complete prepared build atomically.

Prepared builds use `AUDISOR_DATA_DIR`. When it is unset, the runtime selects
the platform user-data directory rather than a product-local source path:

    <data-root>/builds/<build-id>/instruction.json
    <data-root>/builds/<build-id>/plan.json
    <data-root>/builds/<build-id>/skills/<task-id>-<slug>/SKILL.md

Generated skills are build artifacts and are never installed into permanent
.agents/skills directories. A blocked plan returns HTTP 200 with specific gaps,
persists instruction.json and plan.json, and generates no task skills.

Preparation also publishes `integrity.json` inside the same atomic staging
directory. It is an unsigned SHA-256 consistency anchor over the exact
instruction, plan, task records, and rendered skills. The runtime rejects legacy
or altered builds without silently regenerating or repairing that anchor.

## Isolated prepared-build execution

The runtime exposes:

    POST /v1/builds/{build_id}/executions

The endpoint binds a prepared build to an explicit target root and allowed
write paths, records a target baseline, copies that baseline into a per-
execution workspace, re-verifies preparation integrity, and executes tasks
sequentially in deterministic dependency order. Workers continue to receive the
minimal `{task_id, prompt}` task boundary; their answer contains a strict
JSON action plan that is parsed completely before local actions begin.

Only these action types are accepted:

    write_file
    create_directory
    delete_file

Filesystem effects are resolved against the isolated workspace and its allowed
paths. Expected outputs, planned and actual changed paths, write authority,
target-baseline preservation, hashes, and terminal evidence are verified
deterministically. Prepared executable validation metadata is hashed and
retained but not executed by this endpoint. Python, tests, scripts, shells, and
arbitrary commands are not run by the execution endpoint.

Durable execution data is stored beneath the prepared build:

    <data-root>/builds/<build-id>/executions/<execution-id>/
        authority.json
        baseline.json
        workspace.json
        state.json
        results/<task-id>.json
        evidence/<task-id>/
        workspace/

The real target is never used as a task write root. Failed prerequisites block
their dependents, interrupted running tasks are not retried, and identical
idempotent requests return the existing durable state.

## Roadmap

The following capabilities are intentionally not part of the current runtime and
are reserved for future work: executable validation, real-target apply, retries,
resume, parallel execution, queues and percentage progress, A-Flow policy,
UiPath-derived orchestration, Audisor/Edge governance, evidence UI, frontend
work, deployment, and production packaging.
