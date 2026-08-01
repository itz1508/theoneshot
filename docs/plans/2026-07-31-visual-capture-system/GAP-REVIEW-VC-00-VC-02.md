# VC-00 through VC-02 — Gap Review and Plan Amendment

Status: `validated_with_required_amendments`

Review ID: `GR-VC-00-02-2026-07-31`

Reviewed branch: `agent/visual-capture-build-plans`

Reviewed plan head: `ab0e6ee545eece8984ae4dde4cbd5debf4097176`

Applies to:

- `BUILD-00-backend-foundation.md`
- `BUILD-01-framework-extraction.md`
- `BUILD-02-html-generation-and-verification.md`
- `state/VC-00.json`
- `state/VC-01.json`
- `state/VC-02.json`

Normative companion:

- [`SUCCESS-CRITERIA-VC-00-VC-02.md`](SUCCESS-CRITERIA-VC-00-VC-02.md)

## 1. Review conclusion

The product decomposition and dependency graph are valid. `VC-00` is the correct contract-and-recovery foundation, and `VC-01` and `VC-02` may proceed in parallel only after the `VC-00` freeze manifest is accepted.

The plans were not implementation-ready as written because the common lifecycle was not mapped to the domain phases, task execution fields were inconsistently explicit, success criteria were not versioned, and the state instances did not conform to their own state template.

This document and the linked success-criteria document are a normative plan amendment. Where the original build documents are less specific, this amendment controls.

Build readiness after this amendment:

| Build | Plan review | Implementation readiness |
|---|---|---|
| `VC-00` | valid with amendments | ready for repository preflight only |
| `VC-01` | valid with amendments | blocked until `VC-00` freeze |
| `VC-02` | valid with amendments | blocked until `VC-00` freeze |

No implementation code is authorized by this review.

## 2. Confirmed gaps

### GR-001 — State records diverged from the state template

Evidence:

- the template requires repository observation flags, full validation fields, recovery hashes, snapshot requirements, non-idempotent step tracking, and execution timestamps;
- the three state instances omitted several of those fields and introduced inconsistent repository keys.

Root cause:

The build states were initialized as shortened planning summaries instead of authoritative recovery records.

Repair:

The three state files are normalized to the template structure and record this gap review and success-criteria artifact as required preflight inputs.

Success proof:

- each state file parses as JSON;
- each contains every required top-level template section;
- the next executable step is recoverable without chat history;
- unresolved decisions remain explicit rather than being silently resolved.

### GR-002 — Common lifecycle was declared but not mapped to domain phases

Evidence:

The master plan requires eight lifecycle stages, while each build uses domain-specific phases with no authoritative mapping.

Root cause:

The plans describe what to build but do not consistently describe how every domain task passes through proof-first testing, validation, review, and handoff.

Repair:

The lifecycle mapping in section 4 of this amendment is normative for all three builds.

Success proof:

No domain task may be marked complete from implementation output alone. It must record proof-first evidence, targeted validation, applicable integration validation, diff review, and a state checkpoint.

### GR-003 — Task execution contracts were incomplete or inconsistent

Evidence:

The master plan says every task states objective, exact paths, exclusions, prerequisites, evidence, failure classification, rollback, and completion. Many tasks include only steps and partial completion language.

Root cause:

The build documents rely on implicit plan context rather than one enforceable inherited execution contract.

Repair:

The inherited task contract in section 5 applies to every task. A task-specific statement overrides the inherited default only when it is more restrictive.

Success proof:

An implementation agent must be able to construct a complete task record before mutation. If exact owned paths or prerequisites cannot be resolved, the task remains blocked.

### GR-004 — Success criteria were broad and mutable

Evidence:

The original completion criteria describe outcomes but do not assign stable IDs, freeze thresholds, or distinguish pre-build specification from post-build proof.

Root cause:

Completion criteria were written as plan prose rather than a versioned verification contract.

Repair:

`SUCCESS-CRITERIA-VC-00-VC-02.md` defines immutable criteria IDs, required evidence, failure states, and cross-build closure conditions.

Success proof:

The success criteria begin as `specified_not_run`. They can become `passed` only from recorded execution evidence. Criteria changes after implementation begins require a new version and amendment history.

### GR-005 — The `VC-00` freeze boundary was not represented by one authoritative artifact

Evidence:

The plans require freezing contracts, schemas, fixtures, errors, evidence vocabulary, and ownership, but do not require one manifest binding all identities together.

Root cause:

Freeze requirements were distributed across tasks and state notes.

Repair:

`VC-00` must produce `contract-freeze-manifest.json` containing:

- contract version and schema hashes;
- fixture IDs and hashes;
- error and evidence vocabulary versions;
- adapter-port signatures;
- public API/CLI/MCP schema hashes;
- exact shared and build-owned paths;
- accepted success-criteria version;
- repository commit and operation identity.

Success proof:

`VC-01` and `VC-02` must reject startup when any recorded freeze identity differs.

### GR-006 — Parallel ownership was described but not executable

Evidence:

Candidate paths exist, but exact owned paths remain empty in the state files until repository preflight.

Root cause:

The planning branch cannot prove active repository ownership without implementation-time inspection.

Repair:

This remains an intentional preflight blocker. `VC-00` must freeze an ownership map before either parallel build mutates files.

Success proof:

- every target path has one owner;
- shared schemas are read-only to `VC-01` and `VC-02`;
- overlapping writes fail before mutation;
- an amendment returns to `VC-00` and increments the contract version.

### GR-007 — Cross-build integration did not have a single closure fixture

Evidence:

Each build has local fixtures, but the coordinated system lacked one mandatory end-to-end fixture proving the full path.

Root cause:

Integration was described as a handoff rather than a complete product proof.

Repair:

The cross-build closure fixture is:

```text
raw UI intent + one selected image or HTML source
→ VC-01 evidence-anchored UI contract
→ VC-02 self-contained Audisor preview
→ approved interaction fixture replay
→ completed-build structural capture
→ expected-versus-actual comparison
→ root-cause repair guide for an intentional overlay defect
```

Success proof:

The fixture must pass without changing the frozen contract and must preserve one operation/evidence chain across both builds.

### GR-008 — Three product decisions remain intentionally unresolved

These are not engineering choices and must not be silently selected by an implementation agent.

| Decision | Required choice | Blocking point |
|---|---|---|
| `DEC-001` deployment class | local/single-user resumable tool, or multi-user service | before persistence and security architecture is frozen |
| `DEC-002` vision boundary | provider policy, retention, cost limits, and local-only fallback | before a live vision adapter is enabled |
| `DEC-003` VC-02 delivery boundary | standalone artifact/API/CLI/MCP is sufficient, or existing `web/` integration is required | before VC-02 final delivery acceptance |

The plans may implement deterministic fakes and provider-neutral ports before these decisions, but may not claim final delivery completion while the applicable decision is unresolved.

## 3. Findings that are not gaps

The following are valid and remain unchanged:

- a standalone `audisor_visual` package is preferred but must be proven during preflight;
- the legacy runtime remains excluded;
- WebContainer is optional and cannot own backend truth;
- image-provider output is not measured evidence;
- source visual identity must not be copied;
- browser geometry may be measured only for the captured viewport;
- unavailable or unauthorized sources must not be inferred;
- `VC-03` remains outside this review except for dependency preservation.

## 4. Normative lifecycle mapping

Domain phases do not replace the common lifecycle. Every domain task passes through the lifecycle below.

| Common lifecycle | `VC-00` mapping | `VC-01` mapping | `VC-02` mapping |
|---|---|---|---|
| `preflight` | Phase 0 | Phase 0 | Phase 0 |
| `contract_and_fixture_review` | Phase 2 and Phase 5.1 review before freeze | VC-00 freeze verification plus applicable Phase 9 fixtures | VC-00 freeze verification plus applicable Phase 10 fixtures |
| `proof_first_tests` | failing schema/store/port/public-surface tests before implementation | failing extraction/pattern/wireframe/guidance tests before each implementation slice | failing render/interaction/capture/comparison tests before each implementation slice |
| `implementation` | Phases 1–4 | Phases 1–8 | Phases 1–9 |
| `targeted_validation` | targeted validation section | Phase 9.1–9.2 | Phase 10.1–10.2 |
| `integration_validation` | fake-adapter end-to-end and parallel handoff | Phase 9.3 | Phase 10.3 |
| `diff_and_license_review` | scoped diff, dependency, protected-path, and public-surface review | scoped diff, provider/dependency, no-copy, and license review | scoped diff, browser/WebContainer dependency, source-safety, and no-copy review |
| `checkpoint_and_handoff` | freeze manifest plus VC-01/VC-02 state initialization | terminal state plus VC-02 integration identity | terminal state plus delivery bundle and integration identity |

## 5. Inherited task execution contract

Every task in `VC-00`, `VC-01`, and `VC-02` inherits the following fields.

### Objective

The task heading and its declared output define the objective. The state record must restate the objective before mutation.

### Exact owned paths

The task may mutate only paths assigned to its build in the accepted `VC-00` ownership map. Candidate paths are not authority. An empty ownership map blocks mutation.

### Excluded paths

The build-level excluded paths, protected repository paths, frozen shared contracts, and paths owned by another parallel build are always excluded.

### Prerequisites

- previous required task checkpoint passed;
- accepted success-criteria version recorded;
- required contract/fixture identities match state;
- literal repository status and target hashes recorded;
- required product decisions resolved at the point where they become material.

### Proof-first requirement

Before implementation, add or identify a deterministic failing test or fixture that proves the missing behavior. Record its command, expected failure, and criterion IDs.

### Expected evidence

- changed-path list;
- before/after hashes where applicable;
- test command and exit code;
- produced artifact IDs;
- failure or unavailable classification;
- state checkpoint and next executable step.

### Failure classification

Use the build error vocabulary. A failure must be recorded as `blocked`, `failed`, `uncertainty`, or a defined machine error. It must not be converted to completion through prose.

### Rollback boundary

Restore only task-owned files to pre-task hashes or the approved snapshot. Preserve failed attempts, logs, and artifacts. Never roll back unrelated dirty work.

### Completion

A task completes only when its task-specific completion statement and all referenced success-criteria IDs pass. File presence or generated output alone is insufficient.

## 6. Cross-build authority rules

1. The locked user/product boundary controls expected behavior.
2. The repository controls current facts, active ownership, constraints, and feasibility.
3. The `VC-00` freeze manifest controls shared implementation contracts.
4. The success-criteria document controls verification.
5. Build states control continuation and recovery.
6. Generated previews, screenshots, and reports are evidence, not authority by themselves.
7. Any required shared-contract change stops both parallel builds and returns to a versioned `VC-00` amendment.

## 7. Review closure

This gap review is closed as a planning review when:

- this amendment and the success-criteria document exist on the planning branch;
- `VC-00`, `VC-01`, and `VC-02` states reference both artifacts;
- the state instances conform to the state template structure;
- remaining product decisions are explicit;
- implementation remains blocked from bypassing repository preflight or the `VC-00` freeze.

The implementation gaps themselves remain `specified_not_run` until code and execution evidence exist.
