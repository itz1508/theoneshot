# Theoneshot

Theoneshot is a platform for closing the gap between a plan and a verified
result. It fills the gaps before execution and delivers one clean, verified fix.

The container images are coordinated at version **0.9.0** (release candidate).
The `audisor` Python package is at **0.10.0** (legacy runtime tombstoned).
All products are independent and separately deployable.

## Products

| Product | Package | Location | Purpose |
|---|---|---|---|
| A-Flow | `theoneshot-aflow` | `openai_project/aflow/` | Plan-readiness analysis: admit and adversarially analyze a plan, verify revision closure, lock an accepted plan, detect drift, and evaluate build evidence. |
| OneShot Fix | `audisor-backend` | `audisor_backend/` | Governed, issue-scoped build/fix execution (the canonical Fix engine). |
| Legacy Audisor Runtime | `audisor` | `openai_project/runtime/` | Legacy BYOK/BYOM model-execution API (tombstoned in 0.10.0); /health and /ready only. |
| Audisor Toolkit | `audisor-local` | `audisor/` (submodule) | Tokenless, read-only local repository inspection: scan, inspect, trace, normalize, validate, replay (CLI + MCP). |

Dependencies: A-Flow is standalone. The legacy runtime optionally depends on the Fix
engine and degrades gracefully (`fix_engine_unavailable`) when it is absent. The
Fix engine depends on the legacy runtime and A-Flow. The toolkit is standalone.

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

## OneShot Fix + Legacy Audisor Runtime

The Fix engine installs as an optional dependency of the legacy runtime. From
`openai_project/runtime`:

```powershell
cd openai_project/runtime
uv sync --extra dev --locked
uv pip install -e ../../audisor_backend
uv run uvicorn audisor.main:app --host 127.0.0.1 --port 8000
```

The legacy runtime exposes `GET /health` and `GET /ready` only. All other
endpoints are tombstoned in 0.10.0.
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
openai_project/runtime/   legacy Audisor Runtime (package `audisor`, tombstoned in 0.10.0)
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
