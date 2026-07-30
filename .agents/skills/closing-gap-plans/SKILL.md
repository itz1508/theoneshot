---
name: closing-gap-plans
description: >
  Pre-build repair planning for a *confirmed* gap or defect — closing a bug,
  missing feature, doc gap, or spec mismatch — before any code is touched.
  Layers gap-specific discipline (verified-gap gate, root cause analysis,
  append-only Gap Record) on top of planning-tasks, which owns plan structure.
  Use when the user asks to plan a gap/defect repair or hands over a bug or
  incident report to be scoped. For verifying and closing a gap after the fix
  is built, use closing-gap-execution. For planning new work that is not a
  confirmed defect, use planning-tasks directly.
---

# Closing Gap Plans

Pre-build half of gap closure. Does not duplicate plan structure — read and
follow `planning-tasks` (Clarify Intent through Execution Handoff) for
everything not listed below. Once the fix is built and verified, hand off to
`closing-gap-execution` for fixture proof and closure.

## Combined cycle

```
[planning-tasks]         Clarify Intent → Source of Truth →
[this skill]             Confirm Gap → Gap Record opened → Root Cause →
[planning-tasks]         Scope → Smallest Solution → Tasks → Fix → Verify →
[closing-gap-execution]  Fixture Test → Evaluate → Pattern Capture → Gap Record closed
[planning-tasks]         → Completion Criteria met → Done
```

Any stage can exit early into Blocked / Failed / Timeout per
`planning-tasks` "When Resolution Fails".

Announce at start:
> Using the closing-gap-plans skill (with planning-tasks for plan structure).

---

## Confirm Gap

*Slots in: immediately after Source of Truth, before anything else.*

The defect claim must be verified before proceeding. Accept as
**confirmed** only when at least one of these holds:

1. **Reproduced** — the defect is observable through a test failure,
   runtime error, or incorrect output that can be re-run
2. **Contract violation** — a test, schema, or spec defines expected
   behavior and the current code fails it
3. **User-confirmed** — the user explicitly stated the gap in this
   session and it is specific enough to check (not "something is wrong
   with X")

If none of these hold, the gap is unconfirmed. Stop and investigate
before planning a repair — this skill's input is a confirmed gap, not
an unverified claim.

Investigation tasks (per planning-tasks) may resolve implementation
details, file locations, or which asset to reuse — they must never
prove whether the original gap exists. If the gap itself is
unconfirmed, that is a separate investigation, not this skill's input.

---

## Gap Record

*Slots in: opened right after Confirm Gap.*

### Storage — required, not optional

A different agent or provider may pick up a later stage with no memory
of this session. The Gap Record must live on the filesystem where any
agent can find and append to it by path — never in conversation memory.

Use the repository's existing convention if one exists. Otherwise default
to `docs/plans/<plan-name>/gap-record.md`, alongside the plan. State the
actual path at the top of the record and reference it from the plan
header.

### Two separate objects — do not merge

| Object | Mutability | Purpose |
|--------|-----------|---------|
| **Gap Record** | Append-only | Audit trail — one timeline per gap, never edited or deleted, includes failed/blocked/timeout entries alongside success |
| **Context State** | Mutable | Current-state flag per build: `{ build_id, context_included: bool }`. Governs whether a closed build's detail feeds downstream context. Never store inside the Gap Record — a toggled flag corrupts append-only |

### Entry format

```markdown
### [ISO 8601 UTC timestamp] — [state]
<!-- Format: YYYY-MM-DDTHH:MM:SSZ — sortable, unambiguous, timezone-consistent across agents -->
Gap: [one line, links to plan]
Actor: [which agent/provider, e.g. "Claude", "Codex CLI", "human"]
State: confirmed | in_progress | blocked | failed | uncertainty (timeout) | validated | closed
Evidence at this point: [pointer to Evidence section / fixture result]
Note: [what changed since the last entry]
```

### Rules

1. **Every state transition gets an entry** — including blocked, failed,
   and uncertainty. A record with only success entries misrepresents
   history.
2. **Never edit a prior entry** — append a new one.
3. **Closing entry** (written by `closing-gap-execution`) must reference
   the fixture test result directly (pass/fail, command, timestamp), not
   a summary claim.
4. **Treat every entry as historical fact about a past state**, never
   current state — re-verify before relying on it.
5. **Actor field enables trust calibration** — an entry from an unknown
   or unfamiliar actor warrants more scrutiny than one from a known,
   validated pipeline. Attribution is what makes that judgment possible.

### Context State — storage and lifecycle

**File:** `docs/plans/<plan-name>/context-state.json`, alongside the
Gap Record. One file per build.

**Format:**
```json
{
  "build_id": "<plan-name>",
  "context_included": false
}
```

**Owner:** `closing-gap-execution` writes it when closing the Gap
Record. No other stage modifies it.

**Consumer:** Any downstream agent or model loading context checks
this file before including a closed build's detail. If
`context_included` is `true`, include it. If `false`, exclude it.
If the file doesn't exist, default to `true` (include) — absence
means closure hasn't run yet.

**Rule —** `context_included = false` ONLY WHEN ALL of:

1. build.state is validated or closed
2. `closing-gap-execution` has validated and closed this build
   (not merely finished — actually closed)
3. this build's ID does not appear in any currently
   in_progress / blocked / failed plan's "Depends on plans" field

A build loses context when nothing still working depends on it **and**
closure has been verified — not the moment the build finishes.

This is a **plan-level** field (one gap depending on another gap) —
distinct from planning-tasks' per-task `Depends on: Task X`, which only
orders tasks within one plan and cannot answer this check.

---

## Root Cause

*Slots in: right after Confirm Gap / Gap Record opened, before Scope.*

Write the Root Cause section **inside the plan file, between the Plan
Header and Scope of Work.** This placement ensures any agent reading
the plan encounters the cause before the solution scope — the cause
constrains what Scope is valid.

State the cause, not just the symptom, before scoping any fix:

```markdown
## Root Cause
- Observed symptom: [what is seen]
- Root cause: [why it happens — actual defect location/mechanism]
- Why the fix targets the cause, not the symptom: [one line]
```

If root cause cannot be determined from available evidence, this is an
investigation task, not an implementation task. Do not scope a fix
against an unconfirmed cause.

---

## Handoff back to planning-tasks

After Root Cause is written and the Gap Record is opened, resume
`planning-tasks` at **Scope of Work** — do not re-enter from Clarify
Intent or Source of Truth (those stages already completed). The Gap
Record path and Root Cause section are now part of the plan context;
proceed with Scope, Smallest Solution, Tasks, and the remaining
planning-tasks stages in order.

---

## When closing-gap-execution is not available

If `closing-gap-execution` is not installed or not accessible, the
Gap Record cannot be closed through the standard path. In this case:

1. Complete the plan through planning-tasks (Scope through Verify)
2. Append a final Gap Record entry with state `validated` — not
   `closed` — noting that fixture proof was not run through the
   standard closure skill
3. State in the plan header that closure was incomplete and what
   remains unverified

Do not mark the gap `closed` without fixture proof. A gap without
closure stays `validated` — it is not finished, only partially done.

---

## Self-Review additions

On top of planning-tasks' Self-Review list, add:

12. Does every task in the plan help close the *stated gap* specifically
    (not just a generic goal)?

Fixture-test, pattern-capture, and gap-closure checks live in
`closing-gap-execution`'s own Self-Review — not duplicated here, since
this skill's output is a plan, not a verified closure.
