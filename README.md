# Theoneshot

Theoneshot is a platform for closing the gap between a plan and a verified
result. It fills the gaps before execution and delivers one clean, verified fix.

The container images are coordinated at version **0.9.0** (release candidate).
The `audisor` Python package is at **0.10.0** (legacy runtime tombstoned).
All products are independent and separately deployable.

## Products

| Product | Package | Location | Status | Purpose |
|---|---|---|---|---|
| A-Flow | `theoneshot-aflow` | `openai_project/aflow/` | Active | Standalone deterministic plan-readiness and locking product: admit and adversarially analyze a plan, verify revision closure, lock an accepted plan, detect drift, and evaluate build evidence. |
| OneShot Fix | `audisor-backend` | `audisor_backend/` | Active | Governed, issue-scoped build/fix execution (the canonical Fix engine). |
| Audisor Toolkit | `audisor-local` | `audisor/` (submodule) | Active | Tokenless, read-only local repository inspection: scan, inspect, trace, normalize, validate, replay (CLI + MCP). |
| Legacy Audisor Runtime | `audisor` | `openai_project/runtime/` | Deprecated, tombstoned in 0.10.0 | Legacy BYOK/BYOM model-execution API; only `/health` and `/ready` remain for tombstone/status reporting. |

Dependencies: A-Flow is standalone. The Fix engine is standalone. The legacy
runtime optionally depended on the Fix engine and degraded gracefully
(`fix_engine_unavailable`) when it was absent; the runtime is now tombstoned
and retained only for compatibility/reference pending planned removal. The
toolkit is standalone.

## Prerequisites

- Python 3.11+
- [`uv`](https://docs.astral.sh/uv/) (dependency management and execution)
- Docker (only for building the container images)

## A-Flow

```powershell
cd openai_project/aflow
uv sync --extra dev --locked
uv run aflow --help
uv run aflow demo
```

See [openai_project/aflow/README.md](openai_project/aflow/README.md).

## OneShot Fix

The Fix engine is a standalone product:

```powershell
cd audisor_backend
uv sync --locked
uv run pytest
```

See [audisor_backend/](audisor_backend/).

## Legacy Audisor Runtime (Deprecated)

The legacy Audisor Runtime execution service is deprecated and was tombstoned
in version 0.10.0. Only `/health` and `/ready` remain operational for
tombstone/status reporting. The three legacy POST endpoints all return
`410 legacy_runtime_deprecated`:

- `POST /v1/tasks`
- `POST /v1/builds/prepare`
- `POST /v1/builds/{build_id}/executions`

New integrations must not use those routes. Retained source is temporary
compatibility/reference code pending planned removal.

For tombstone-status verification only:

```powershell
cd openai_project/runtime
uv sync --extra dev --locked
uv run uvicorn audisor.main:app --host 127.0.0.1 --port 8000
```

Valid responses: `GET /health` returns `{"status":"ok"}`, `GET /ready` reports
readiness. All other endpoints return `410 legacy_runtime_deprecated`.

See [openai_project/README.md](openai_project/README.md).

## Audisor Toolkit

The toolkit is a Git submodule. From `audisor/backend`:

```powershell
cd audisor/backend
uv sync --locked
uv run audisor --help
uv run python ../scripts/run_demo.py --output-root ../demo-output
```

See [audisor/README.md](audisor/README.md) and
[audisor/backend/README.md](audisor/backend/README.md).

## Container images

Each product builds from a digest-pinned base with frozen dependencies:

| Image | Dockerfile |
|---|---|
| `theoneshot-aflow:0.9.0` | `packaging/aflow/Dockerfile` |
| `theoneshot-fix:0.9.0` | `packaging/oneshot-fix/Dockerfile` |
| `theoneshot-audisor-agent:0.9.0` | `audisor/docker/Dockerfile` |

Build and smoke-check all three from the repository root:

```powershell
pwsh packaging/build-images.ps1
```

## Tests

```powershell
uv run --directory openai_project/aflow pytest
uv run --directory openai_project/runtime pytest -m "not live_fireworks and not live_local"
uv run --directory openai_project/runtime pytest ../../audisor_backend/tests
uv run --directory audisor/backend pytest
```

Live adapter tests run only when the required provider variables are present;
they are deselected by default.

## Repository layout

```text
openai_project/runtime/   Legacy Audisor Runtime, tombstoned in 0.10.0
openai_project/aflow/     A-Flow (package `theoneshot-aflow`)
openai_project/schemas/   JSON schemas for tasks, builds, executions, evidence
openai_project/docs/      Architecture and lifecycle documentation
openai_project/infra/     Sandbox validation image (not a product)
audisor_backend/          OneShot Fix engine (package `audisor-backend`)
audisor/                  Audisor Toolkit submodule (package `audisor-local`)
packaging/                Container packaging + build script
docs/submissions/         OpenAI Build Week 2026 submission record
```

## Agent instructions

This repository is agent-operated. See [Agents.md](Agents.md) for the
authoritative repository layout, the A-Flow lifecycle, protected surfaces, and
the skills index. Read it before making non-trivial changes.
