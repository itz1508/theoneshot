# Automatic A-Flow lifecycle for Codex

For every qualifying repository mutation task, primary Codex drafts a bounded
plan, invokes the project-scoped `aflow` agent read-only, accepts a complete
analysis, computes the primary-owned SHA-256 lock, and only then implements.
After implementation, primary Codex returns trusted evidence to A-Flow before
reporting completion.

The immutable user task remains the source of intent. A-Flow may derive a
success definition, execution trajectory, validation cases, fixture
specifications, and canonical lock payload, but it never implements, locks,
or authorizes execution. Primary Codex owns those actions.

The frozen `openai_project/aflow` package is never imported as a mutable
implementation surface. The integration uses its documented `analyze`,
`close`, and `evaluate-result` CLI interfaces and maps readiness/evaluation to
the package's existing decision vocabulary.

The locked contract retains both the frozen `aflow_decision` and its normalized
`contract_decision`. `material_gap_found` maps to `revision_required`,
`missing_evidence` maps to `uncertainty`, and `contradicted` and
`drift_revalidation_required` retain their meanings. Unknown or inconsistent
pairs are rejected; the frozen schemas remain unchanged.

## Enforcement and limit

`.codex/agents/aflow.toml` supplies the read-only specialist. `.codex/hooks.json`
enables a `PreToolUse` guard that denies recognizable mutation attempts when
`.codex/aflow-state/active-lock.json` is absent or invalid. Codex requires
project trust and local hook review before non-managed hooks run; use `/hooks`
in a new Codex session to trust this repository hook. A repository config can
require and guard the lifecycle, but it cannot independently force a model
subagent to spawn: primary Codex performs the automatic invocation.

The guard is intentionally conservative. It is a fail-closed lifecycle guard,
not an OS security boundary; the agent's read-only sandbox and Codex permission
mode remain the execution security controls.
