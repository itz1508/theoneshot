# A-Flow Lifecycle

Optional repository workflow for validating actionable artifact drafts
(plans, designs, specs that implementation will follow) before coding.
Loaded only when the active Audisor configuration enables A-Flow; see
"Enablement state" below. Referenced from the root `Agents.md` section
"Optional A-Flow workflow".

## Enablement state

A-Flow is controlled by the `aflow_enabled` boolean in the Audisor
configuration file:

* Windows: `%LOCALAPPDATA%\Audisor\config.json`
* POSIX: `$XDG_CONFIG_HOME/audisor/config.json` (default `~/.config/audisor/config.json`)
* Override: the `AUDISOR_CONFIG_PATH` environment variable

When the file is absent, A-Flow defaults to enabled. Inspect or change the
state with the runtime CLI: `audisor aflow status`, `audisor aflow on`,
`audisor aflow off`. The runtime freezes the value once per operation via
`read_frozen_audisor_policy()` (`openai_project/runtime/src/audisor/audisor_lifecycle/operation.py`);
mid-operation toggle changes do not affect a running operation.

* Enabled → load this skill into active context and follow it.
* Disabled → do not load or invoke A-Flow; continue without it and do not
  report an A-Flow outcome.

Tool availability does not imply enablement, and enablement does not imply
the current provider exposes the required MCP tools.

## Provider surface

The `aflow_submit_artifact` and `aflow_last_result` MCP tools are served by
the `aflow-runtime` server, registered repo-scoped for Codex in
`.codex/config.toml`. Other harnesses (for example Qoder) do not expose this
server unless separately registered; for them, follow "Provider lacks the
tool" below.

## When a draft is ready

Submit only a completed actionable draft: a plan, design, or spec that
implementation will follow, with its intent and the relevant repository
context assembled. Read-only factual or inspection tasks do not submit
artifacts. An incomplete draft is not submitted; continue drafting.

## Calling `aflow_submit_artifact`

Call the tool with `status: "draft_complete"`, the full artifact text, its
intent, and the relevant repository context. The runtime engine
(`openai_project/runtime/src/audisor/audisor_lifecycle/artifact_flow.py`)
owns the stage order: gap finding, gap fixing, the unresolved-gap barrier,
evaluation, success criteria, then fixture design.

## Reading the result

Act on the result status:

* `improved` — implement against the improved artifact, its success
  criteria, and its fixture cases. The handoff package is advisory: it is
  execution-ready because every gap was fixed, evaluation passed, and
  success criteria plus fixture cases exist.
* `unresolved_gap` — report each listed gap's requirements
  (`required_to_resolve`, `successful_resolution`) to the user; once the
  inputs exist, submit the revised artifact as a new cycle.
* `skip` — the artifact was not a completed draft; continue drafting.
* `error` — report the stage and detail; use `aflow_last_result` to
  reattach to the most recent persisted result after a transport timeout.

## Provider lacks the tool

When A-Flow is enabled but the active provider does not expose the
`aflow-runtime` tools, do not silently skip the gate and do not stall
searching for the tool. State explicitly that the A-Flow gate is
unavailable on the current provider, present the completed draft to the
human for review before implementation, and record the outcome as
`unavailable`.

## Outcome vocabulary

* `pass` — a real `aflow_submit_artifact` call returned `improved` and the
  result was verified.
* `block` — the call returned `unresolved_gap` or `error`; implementation
  does not proceed against the blocked draft.
* `unavailable` — A-Flow is enabled but the provider exposes no
  `aflow-runtime` tools; the human-review fallback above applies.
* `disabled` — `aflow_enabled` is false for the frozen operation; A-Flow is
  neither loaded nor invoked.

Never report A-Flow as passed unless an actual result was produced and
verified. `unavailable` and `disabled` are not `pass`.

## Evidence

Lifecycle results are persisted by the runtime under
`.codex/audisor-state/artifacts/` (gitignored runtime state). For every
submission, preserve the result status, the submitted artifact identity,
and — on `pass` — the improved artifact, success criteria, and fixture
cases relied on for implementation. For `unavailable` or `disabled`, record
that state in the operation report instead of an A-Flow result.
