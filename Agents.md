# Agents.md — Theoneshot (repo instance)

This file is stored at `D:\Dev\Theoneshot\Agents.md`. It loads **in addition to** the global `Agents.md` under that file's Authority order. It adds repo-specific data only. It does not redefine evidence, mutation, status vocabulary, or reporting rules — those come from the global file and are inherited unchanged.

## Repo layout

Evidence: verified 2026-07-21 by direct filesystem inspection.

* `openai_project/runtime/` — Audisor Agent runtime. Python package `audisor` under `src/audisor/`. Submodules: `adapters`, `api`, `audisor_lifecycle`, `builder`, `codex`, `config`, `operations`, `policies`, `routing`, `schemas`, `security`, `workers`. This is the canonical runtime surface.
* `audisor_backend/` — OneShot Fix engine. Python package `audisor_backend`. Submodules: `adapters`, `artifact_store`, `controllers`, `phases`, `policies`, `sandbox`, `scanning`, `schemas`. Installed as an optional dependency of the runtime.
* `audisor/` — standalone Audisor MCP toolkit (Git submodule pointing to `https://github.com/itz1508/audisor.git`). Contains the scanner, inspector, tracer, normalizer, validator, and replay tools. Also hosts Codex agent definitions and skills for the standalone toolkit.
* `openai_project/aflow/` — A-Flow source and schemas. Separate Python package.
* `openai_project/schemas/` — JSON schemas for tasks, builds, executions, and evidence.
* `openai_project/docs/` — architecture and lifecycle documentation.
* `openai_project/infra/` — sandbox Dockerfile.
* `packaging/oneshot-fix/` — container packaging for the OneShot Fix image (`Dockerfile`, `runtime-requirements.txt`, `backend-requirements.txt`).
* `.codex/config.toml` — workspace-level Codex configuration. Registers the `aflow-runtime` MCP server (canonical A-Flow artifact lifecycle) and sets `hooks = false`.
* `.codex/audisor-state/` — gitignored runtime state; lifecycle results are persisted under `artifacts/`.
* `audisor/.codex/config.toml` + `audisor/.codex/agents/{explorer,reviewer,validator}.toml` — the standalone toolkit's agent-definition source of truth. Role authority lives here, not at root.

## Lint / test

Tests exist in multiple subprojects:

* `audisor_backend/tests/` — Fix engine tests (run with `pytest`)
* `openai_project/runtime/tests/` — runtime tests (run with `pytest`)
* `openai_project/aflow/tests/` — A-Flow tests (run with `pytest`)

No single root-level lint configuration is established. Each subproject manages its own test and lint tooling via `uv`.

## Snapshot lifecycle

Before any build action, create a repository snapshot. Each new snapshot must replace the previous snapshot completely; do not carry forward files from an older snapshot. After every successful Git commit, delete the snapshot.

## Safe removal of non-authoritative residue

After direct inspection proves that an item is generated, machine-local, secret-bearing, duplicate, historical, deprecated, or otherwise non-authoritative, remove it in place when removal cannot affect active runtime behavior, public interfaces, packaging, deployment, persisted user data, or intended design. Remove the item as found; do not replace it with guessed behavior or introduce a substitute feature.

Before removal, inventory the exact paths, verify active ownership and references, establish rollback or recovery, and run focused validation. If ownership, impact, recoverability, or design intent is uncertain, stop and report the evidence instead of deleting it. This rule does not authorize changes to protected paths, commits, pushes, or releases.

## Automatic A-Flow lifecycle

When an actionable artifact draft is complete (a plan, design, or spec that implementation will follow), submit it via the `aflow_submit_artifact` MCP tool with `status: "draft_complete"`, the full artifact text, its intent, and the relevant repository context. The runtime engine (`openai_project/runtime/src/audisor/audisor_lifecycle/artifact_flow.py`) owns the stage order: gap finding, gap fixing, the unresolved-gap barrier, evaluation, success criteria, then fixture design.

Act on the result status:

* `improved` — implement against the improved artifact, its success criteria, and its fixture cases. The handoff package is advisory: it is execution-ready because every gap was fixed, evaluation passed, and success criteria plus fixture cases exist.
* `unresolved_gap` — report each listed gap's requirements (`required_to_resolve`, `successful_resolution`) to the user; once the inputs exist, submit the revised artifact as a new cycle.
* `skip` — the artifact was not a completed draft; continue drafting.
* `error` — report the stage and detail; use `aflow_last_result` to reattach to the most recent persisted result after a transport timeout.

Read-only factual or inspection tasks do not submit artifacts.

### Ordered phase continuation

For an authorized multi-phase task, once a phase commit is complete and its post-commit verification succeeds, the agent must continue directly to the next authorized phase in the same run. It must not stop merely to ask whether to start that next phase. “Begin” means perform the next phase’s stated discovery, review gate, and implementation steps; it does not waive any required A-Flow or authority gate, or authorize a commit or push for that phase.

The agent must stop before the next phase’s commit and report the diff and validation for review unless that commit was separately authorized. If post-commit verification fails, preserve the failed transition state, report the evidence, and do not begin the next phase.

## Protected — do not touch without explicit human confirmation

* `.git/`, `.codex/` (both root and `audisor/.codex/`)
* `Agents.md` at any level (global Default protections)
* `audisor/.agents/skills/**/SKILL.md` — **proposed, not yet in the global file's Default protections.** These encode procedure the same way `Agents.md` does — casual edits here are a meta-mutation risk. Treat as instance-protected for now; confirm before promoting to the global file.

## Skills index

Nine skills live under `audisor/.agents/skills/<name>/SKILL.md`. Before starting work whose type matches a skill's purpose, view that skill's full `SKILL.md` first — do not improvise a substitute procedure. If a skill's actual content conflicts with this file or the global `AGENTS.md`, report the conflict before proceeding; do not silently prefer one.

Purposes below are **inferred from the folder name only** — unverified against actual file content. Replace with each `SKILL.md`'s own description before treating this table as authoritative.

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

**Note:** Several skill descriptions internally reference `init` (setup/configuration) and `learn` (explanations) as routing targets. These skills do not currently exist as directories. Until they are created, route setup/configuration requests to `repository-discovery` and explanations to general agent capability.

## Agent roles (Codex)

Root `.codex/config.toml` registers the `aflow-runtime` MCP server. `audisor/.codex/agents/` defines `explorer`, `reviewer`, and `validator`. Both surfaces are authoritative: root config for the A-Flow artifact lifecycle tools, `audisor/.codex/agents/` for the explorer/reviewer/validator agent triad.

`audisor/.codex/agents/` defines three roles:

* `explorer.toml` — read-only discovery/review pass (global file's "spawn a read-only explorer-type agent").
* `reviewer.toml` — plan-review pass (`plan-gap-review`, `requirement-coverage`).
* `validator.toml` — validation pass (`focused-validation`, `validation-gap-review`).

**Gap, flagged not fixed:** the global file describes a "worker" role that may be spawned after the decision gate to perform implementation (`controlled-implementation`). No `worker.toml` or equivalent exists in this listing. The active implementation arrangement is unresolved: the primary Codex agent may implement directly, or a worker role definition may be missing. Do not assume either arrangement until the configuration is inspected or a human confirms the intended design.

---

## MCP Input Schema Strictness

Every public MCP tool in this repository must:

- expose an input schema with `additionalProperties: false`;
- reject unknown properties at runtime;
- prove rejection through a real MCP transport call, not schema inspection alone.

The current implementation hardens FastMCP's generated argument models via internal fields (`server._tool_manager`, `tool.fn_metadata.arg_model`, `tool.parameters`). This is a workaround for a version-specific defect in the pinned FastMCP/MCP SDK, not a general framework truth — later SDK versions may not require it.

**Do not copy this workaround into another project** without first:

1. confirming the installed SDK version's actual default behaviour;
2. adding a regression test that proves rejection through a live transport call.

The regression proof lives at `audisor/backend/recheck/mcp_recheck_proof.py`. Run it after any MCP SDK upgrade or tool-registration change.

---

## Distribution Verification

Before building release evidence or running a clean-install proof:

- remove stale artifacts from `dist/`;
- build the current package version;
- select the expected wheel by **exact filename and version**, never by an unrestricted wildcard or `dist/*.whl` glob;
- install it non-editably into a clean environment **outside** the repository;
- verify the imported package and executable both resolve to that installation, not to the editable source tree.

Stale `dist/` artifacts will cause an older wheel to be installed and silently invalidate the clean-install proof.
