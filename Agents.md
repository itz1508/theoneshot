# Agents.md — Theoneshot (repo instance)

Save as `D:\Dev\Theoneshot\Agents.md`. Loads **in addition to** the global
`Agents.md` per that file's Authority order. This file adds repo-specific
data only. It does not redefine evidence, mutation, status vocabulary, or
reporting rules — those come from the global file and are inherited
unchanged.

## Repo layout

Evidence: `tree /F` output supplied 2026-07-15. Not independently verified
beyond the listing itself — file contents were not inspected.

* `python/audisor_core/` — primary Python package (`gates`, `report`,
  `scan`, `snapshot` submodules). Application code.
* `audisor/` — agent governance workspace only: skills, Codex config. No
  application code of its own.
* `tests/` — currently empty. No test command established.
* `bin/` — currently empty.
* `.codex/config.toml` — workspace-level, **intentionally minimal by
  design**. Confirmed via the file's own header comments (user-supplied,
  not independently executed): it exists specifically to avoid conflicting
  with `audisor/.codex/config.toml`'s agent definitions, not to define
  agents itself.
* `audisor/.codex/config.toml` + `audisor/.codex/agents/{explorer,reviewer,validator}.toml`
  — the actual agent-definition source of truth. Role authority lives here,
  not at root.

## Lint / test

`unknown — not yet established`. `tests/` is empty; no lint config appears
in this listing. Do not assume `pytest` / `ruff` / any specific tool is
wired until confirmed by inspection.

## Snapshot lifecycle

Before any build action, create a repository snapshot. Each new snapshot
must replace the previous snapshot completely; do not carry forward files
from an older snapshot. After every successful Git commit, delete the
snapshot.

## Automatic A-Flow lifecycle

For every non-trivial repository mutation task, primary Codex must inspect the
relevant state, draft a concrete plan, then invoke the project-scoped `aflow`
agent before the first mutation. The `aflow` agent is analysis-only: it derives
success, trajectory, gap findings, validation cases, fixture specifications,
and a canonical lock payload. It never writes implementation files, locks a
plan, or authorizes execution.

Primary Codex accepts or rejects the returned analysis, computes and stores the
SHA-256 lock, implements only the accepted plan, and returns trusted build
evidence to A-Flow before reporting completion. Missing, malformed, or changed
analysis/lock content fails closed. Purely read-only factual and deterministic
inspection tasks do not require the lifecycle.

## Protected — do not touch without explicit human confirmation

* `.git/`, `.codex/` (both root and `audisor/.codex/`)
* `Agents.md` at any level (global Default protections)
* `audisor/.agents/skills/**/SKILL.md` — **proposed, not yet in the global
  file's Default protections.** These encode procedure the same way
  `Agents.md` does — casual edits here are a meta-mutation risk. Treat as
  instance-protected for now; confirm before promoting to the global file.

## Skills index

Ten skills live under `audisor/.agents/skills/<name>/SKILL.md`. Before
starting work whose type matches a skill's purpose, view that skill's full
`SKILL.md` first — do not improvise a substitute procedure. If a skill's
actual content conflicts with this file or the global `AGENTS.md`, report
the conflict before proceeding; do not silently prefer one.

Purposes below are **inferred from the folder name only** — unverified
against actual file content. Replace with each `SKILL.md`'s own description
before treating this table as authoritative.

| Skill | Path (from repo root) | Inferred purpose (unverified) | Maps to (global AGENTS.md) |
|---|---|---|---|
| init | `audisor/.agents/skills/init/SKILL.md` | Session/task bootstrap — load authority files, establish starting state | General task review gate: authority/context |
| repository-discovery | `audisor/.agents/skills/repository-discovery/SKILL.md` | Branch/HEAD/dirty-state/target-path discovery | Repository state |
| active-path-inspection | `audisor/.agents/skills/active-path-inspection/SKILL.md` | Determine what's actually wired (imports/callers/entrypoint) vs. dormant | Active implementation |
| controlled-implementation | `audisor/.agents/skills/controlled-implementation/SKILL.md` | Gated mutation execution | Mutation |
| focused-validation | `audisor/.agents/skills/focused-validation/SKILL.md` | Targeted validator run post-change | Validation |
| validation-gap-review | `audisor/.agents/skills/validation-gap-review/SKILL.md` | Checks whether validation actually proves the intended result, not just that it ran | Validation / Plan mode gap criteria |
| requirement-coverage | `audisor/.agents/skills/requirement-coverage/SKILL.md` | Requirement-by-requirement coverage check | General task review gate: reviewer packet |
| plan-gap-review | `audisor/.agents/skills/plan-gap-review/SKILL.md` | Second-pass plan gap review | Plan mode: Second pass |
| evidence-reporting | `audisor/.agents/skills/evidence-reporting/SKILL.md` | Evidence capture + the five-field report | Evidence / Reporting |
| learn | `audisor/.agents/skills/learn/SKILL.md` | Lesson-capture/approval workflow | Learning capture |

## Agent roles (Codex)

Root `.codex/config.toml` defines no agents by design (see Repo layout).
Role authority is `audisor/.codex/agents/` only — treat root config as a
non-authoritative pass-through, not an alternate or fallback agent
definition.

`audisor/.codex/agents/` defines three roles:

* `explorer.toml` — read-only discovery/review pass (global file's
  "spawn a read-only explorer-type agent").
* `reviewer.toml` — plan-review pass (`plan-gap-review`,
  `requirement-coverage`).
* `validator.toml` — validation pass (`focused-validation`,
  `validation-gap-review`).

**Gap, flagged not fixed:** the global file also describes a "worker" role
spawned after the decision gate to perform implementation
(`controlled-implementation`). No `worker.toml` or equivalent exists in
this listing. Either the primary Codex agent performs implementation
directly with no separate spawned role — a legitimate design — or a role
file is missing. This listing doesn't have enough evidence to say which.
Confirm before assuming either.
