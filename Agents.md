# Agents.md — Theoneshot (repo instance)

This file is stored at `D:\Dev\Theoneshot\Agents.md`. It loads **in addition
to** the global `Agents.md` under that file's Authority order. It adds repo-specific
data only. It does not redefine evidence, mutation, status vocabulary, or
reporting rules — those come from the global file and are inherited
unchanged.

## Repo layout

Evidence: verified 2026-07-21 by direct filesystem inspection.

* `openai_project/runtime/` — Audisor Agent runtime. Python package `audisor`
  under `src/audisor/`. Submodules: `adapters`, `api`, `audisor_lifecycle`,
  `builder`, `codex`, `config`, `operations`, `policies`, `routing`, `schemas`,
  `security`, `workers`. This is the canonical runtime surface.
* `audisor_backend/` — OneShot Fix engine. Python package `audisor_backend`.
  Submodules: `adapters`, `artifact_store`, `controllers`, `phases`, `policies`,
  `sandbox`, `scanning`, `schemas`. Installed as an optional dependency of the
  runtime.
* `audisor/` — standalone Audisor MCP toolkit (Git submodule pointing to
  `https://github.com/itz1508/audisor.git`). Contains the scanner, inspector,
  tracer, normalizer, validator, and replay tools. Also hosts Codex agent
  definitions and skills for the standalone toolkit.
* `openai_project/aflow/` — A-Flow source and schemas. Separate Python package.
* `openai_project/schemas/` — JSON schemas for tasks, builds, executions, and
  evidence.
* `openai_project/docs/` — architecture and lifecycle documentation.
* `openai_project/infra/` — sandbox Dockerfile.
* `packaging/oneshot-fix/` — container packaging for the OneShot Fix image
  (`Dockerfile`, `runtime-requirements.txt`, `backend-requirements.txt`).
* `.codex/config.toml` — workspace-level Codex configuration. Defines the
  `aflow` agent and enables `hooks = true`. This is the root authority
  surface for the A-Flow lifecycle hook integration.
* `.codex/agents/aflow.toml` — A-Flow agent definition. Read-only analysis
  agent for pre-build review and post-build evaluation.
* `.codex/hooks.json` — PreToolUse hook that intercepts mutations and verifies
  active Audisor execution locks via `audisor.audisor_lifecycle.hook`.
* `audisor/.codex/config.toml` + `audisor/.codex/agents/{explorer,reviewer,validator}.toml`
  — the standalone toolkit's agent-definition source of truth. Role authority
  lives here, not at root.

## Lint / test

Tests exist in multiple subprojects:

* `audisor_backend/tests/` — Fix engine tests (run with `pytest`)
* `openai_project/runtime/tests/` — runtime tests (run with `pytest`)
* `openai_project/aflow/tests/` — A-Flow tests (run with `pytest`)

No single root-level lint configuration is established. Each subproject
manages its own test and lint tooling via `uv`.

## Snapshot lifecycle

Before any build action, create a repository snapshot. Each new snapshot
must replace the previous snapshot completely; do not carry forward files
from an older snapshot. After every successful Git commit, delete the
snapshot.

## Automatic A-Flow lifecycle

For every non-trivial repository mutation task, primary Codex must invoke the
project-scoped `aflow` agent before the first mutation. Reuse a structurally
usable supplied plan; create one candidate plan only when none is supplied.
Pass task, plan, applicable authority, and repository context to A-Flow, then
run its returned data through `openai_project/runtime/src/audisor/audisor_lifecycle/ignition.py`.
That layer calls the existing adapter and schema; only its ready, valid contract
permits implementation. Collect the contract-required evidence. A non-ready,
malformed, tampered, or unresolved contract is never execution authority.
Read-only factual or inspection tasks do not invoke the full lifecycle.

## Protected — do not touch without explicit human confirmation

* `.git/`, `.codex/` (both root and `audisor/.codex/`)
* `Agents.md` at any level (global Default protections)
* `audisor/.agents/skills/**/SKILL.md` — **proposed, not yet in the global
  file's Default protections.** These encode procedure the same way
  `Agents.md` does — casual edits here are a meta-mutation risk. Treat as
  instance-protected for now; confirm before promoting to the global file.

## Skills index

Nine skills live under `audisor/.agents/skills/<name>/SKILL.md`. Before
starting work whose type matches a skill's purpose, view that skill's full
`SKILL.md` first — do not improvise a substitute procedure. If a skill's
actual content conflicts with this file or the global `AGENTS.md`, report
the conflict before proceeding; do not silently prefer one.

Purposes below are **inferred from the folder name only** — unverified
against actual file content. Replace with each `SKILL.md`'s own description
before treating this table as authoritative.

| Skill | Path (from repo root) | Inferred purpose (unverified) | Maps to (global AGENTS.md) |
|---|---|---|---|
| repository-discovery | `audisor/.agents/skills/repository-discovery/SKILL.md` | Branch/HEAD/dirty-state/target-path discovery | Repository state |
| active-path-inspection | `audisor/.agents/skills/active-path-inspection/SKILL.md` | Determine what's actually wired (imports/callers/entrypoint) vs. dormant | Active implementation |
| controlled-implementation | `audisor/.agents/skills/controlled-implementation/SKILL.md` | Gated mutation execution | Mutation |
| focused-validation | `audisor/.agents/skills/focused-validation/SKILL.md` | Targeted validator run post-change | Validation |
| validation-gap-review | `audisor/.agents/skills/validation-gap-review/SKILL.md` | Checks whether validation actually proves the intended result, not just that it ran | Validation / Plan mode gap criteria |
| requirement-coverage | `audisor/.agents/skills/requirement-coverage/SKILL.md` | Requirement-by-requirement coverage check | General task review gate: reviewer packet |
| plan-gap-review | `audisor/.agents/skills/plan-gap-review/SKILL.md` | Second-pass plan gap review | Plan mode: Second pass |
| evidence-reporting | `audisor/.agents/skills/evidence-reporting/SKILL.md` | Evidence capture + the five-field report | Evidence / Reporting |
| audisor-plan-review | `audisor/.agents/skills/audisor-plan-review/SKILL.md` | Trigger local Audisor MCP review for a completed implementation plan before coding | A-Flow plan qualification |

**Note:** Several skill descriptions internally reference `init` (setup/configuration)
and `learn` (explanations) as routing targets. These skills do not currently exist
as directories. Until they are created, route setup/configuration requests to
`repository-discovery` and explanations to general agent capability.

## Agent roles (Codex)

Root `.codex/config.toml` defines the `aflow` agent and enables hooks.
`audisor/.codex/agents/` defines `explorer`, `reviewer`, and `validator`.
Both surfaces are authoritative: root config for A-Flow lifecycle hook
integration, `audisor/.codex/agents/` for the explorer/reviewer/validator
agent triad.

`audisor/.codex/agents/` defines three roles:

* `explorer.toml` — read-only discovery/review pass (global file's
  "spawn a read-only explorer-type agent").
* `reviewer.toml` — plan-review pass (`plan-gap-review`,
  `requirement-coverage`).
* `validator.toml` — validation pass (`focused-validation`,
  `validation-gap-review`).

**Gap, flagged not fixed:** the global file describes a "worker" role that may
be spawned after the decision gate to perform implementation
(`controlled-implementation`). No `worker.toml` or equivalent exists in
this listing. The active implementation arrangement is unresolved: the primary
Codex agent may implement directly, or a worker role definition may be missing.
Do not assume either arrangement until the configuration is inspected or a
human confirms the intended design.

---

## MCP Input Schema Strictness

Every public MCP tool in this repository must:

- expose an input schema with `additionalProperties: false`;
- reject unknown properties at runtime;
- prove rejection through a real MCP transport call, not schema inspection alone.

The current implementation hardens FastMCP's generated argument models via
internal fields (`server._tool_manager`, `tool.fn_metadata.arg_model`,
`tool.parameters`). This is a workaround for a version-specific defect in the
pinned FastMCP/MCP SDK, not a general framework truth — later SDK versions may
not require it.

**Do not copy this workaround into another project** without first:

1. confirming the installed SDK version's actual default behaviour;
2. adding a regression test that proves rejection through a live transport call.

The regression proof lives at `audisor/backend/recheck/mcp_recheck_proof.py`.
Run it after any MCP SDK upgrade or tool-registration change.

---

## Distribution Verification

Before building release evidence or running a clean-install proof:

- remove stale artifacts from `dist/`;
- build the current package version;
- select the expected wheel by **exact filename and version**, never by an
  unrestricted wildcard or `dist/*.whl` glob;
- install it non-editably into a clean environment **outside** the repository;
- verify the imported package and executable both resolve to that installation,
  not to the editable source tree.

Stale `dist/` artifacts will cause an older wheel to be installed and silently
invalidate the clean-install proof.
