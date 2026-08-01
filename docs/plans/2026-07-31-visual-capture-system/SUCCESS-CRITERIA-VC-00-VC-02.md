# VC-00 through VC-02 — Versioned Success Criteria

Status: `specified_not_run`

Criteria version: `SC-VC-00-02-1.0.0`

Applies to contract version: `unfrozen`

Authority:

- these criteria are the verification contract for `VC-00`, `VC-01`, and `VC-02`;
- implementation output cannot redefine these criteria;
- once implementation begins, changes require a new criteria version and an append-only amendment record;
- a criterion passes only from recorded execution evidence.

## 1. Result vocabulary

Each criterion has one state:

- `specified_not_run`
- `passed`
- `failed`
- `blocked`
- `unavailable`
- `not_applicable`

`passed` requires the listed evidence. A narrative claim, file existence, screenshot alone, or agent confidence is insufficient.

## 2. Common evidence requirements

Every executed criterion records:

- criterion ID and criteria version;
- operation/build ID;
- repository branch and commit;
- contract and fixture versions;
- exact command or deterministic action;
- exit code or structured result;
- artifact references and hashes;
- observed versus expected result;
- timestamp;
- failure/unavailable reason when not passed.

## 3. VC-00 success criteria — Backend foundation

### `SC-00-001` — Active ownership is proven

Expected:

- one active visual package owner;
- one operation-state owner;
- exact shared, `VC-01`, and `VC-02` path maps;
- no overlap with tombstoned runtime or protected surfaces.

Required evidence:

- repository discovery record;
- literal status and submodule state;
- ownership map with one owner per mutable path;
- protected-path review.

Failure condition:

Any target path has conflicting ownership or requires unauthorized protected-path mutation.

### `SC-00-002` — Package boundary installs cleanly

Expected:

The foundation package installs and imports in editable and non-editable modes without browser, vision, HTML-renderer, WebContainer, or video dependencies.

Required evidence:

- clean-environment install commands;
- import smoke tests;
- dependency manifest proving heavy optional dependencies are absent from the base package.

### `SC-00-003` — Contract freeze manifest is authoritative

Expected:

`contract-freeze-manifest.json` binds:

- contract version;
- schema hashes;
- fixture IDs and hashes;
- error/evidence vocabulary versions;
- adapter signatures;
- public schema hashes;
- ownership map;
- criteria version;
- repository commit.

Required evidence:

- manifest schema validation;
- hash verification;
- a negative test proving a modified schema or fixture causes startup rejection in `VC-01` and `VC-02`.

### `SC-00-004` — Canonical schemas are strict and round-trip safe

Expected:

- valid fixtures round-trip without information loss;
- unknown public fields are rejected;
- unresolved values remain unresolved;
- provider-specific fields cannot enter canonical schemas;
- stable IDs survive serialization.

Required evidence:

- positive, negative, unknown-field, migration, and round-trip tests.

### `SC-00-005` — Operation state is atomic, resumable, and idempotent

Expected:

- atomic creation;
- idempotency-key protection;
- append-only attempts;
- concurrent update protection;
- cancellation and terminal-state enforcement;
- no repeated non-idempotent step after recovery.

Required evidence:

- crash-before-advance fixture;
- concurrent-resume fixture;
- repeated-finalization fixture;
- state-version conflict proof.

### `SC-00-006` — Artifact storage is isolated and verifiable

Expected:

- operation-scoped paths;
- atomic writes;
- SHA-256 identity;
- failed and accepted outputs separated;
- path traversal rejected;
- repeated finalization does not duplicate accepted artifacts.

Required evidence:

- traversal, partial-write, hash-mismatch, duplicate-finalization, and retrieval tests.

### `SC-00-007` — Adapter ports enforce authority boundaries

Expected:

- typed requests/responses;
- capability reporting;
- deterministic unavailable results;
- adapters cannot persist directly;
- adapters cannot mutate shared contracts;
- fake adapters pass one conformance suite.

Required evidence:

- conformance results for every declared port;
- direct-persistence and contract-mutation negative tests.

### `SC-00-008` — Public base surfaces are strict and consistent

Expected:

API, CLI, and MCP expose equivalent operation identity, status, artifacts, unavailable states, and strict field rejection.

Required evidence:

- transport tests;
- CLI exit-code tests;
- live MCP unknown-field rejection;
- cross-surface result-equivalence fixture.

### `SC-00-009` — Fixture corpus is frozen

Expected:

All required canonical fixtures have input hashes, expected-output hashes or structured expectations, evidence state, and criteria mappings.

Required evidence:

- fixture manifest;
- byte/hash verification;
- invalid-provider, crash, and concurrent-resume fixtures passing expected outcomes.

### `SC-00-010` — Parallel handoff is executable

Expected:

- `VC-01` and `VC-02` start against the same freeze manifest;
- each loads its fixtures independently;
- neither can write shared schemas;
- a fake-adapter end-to-end operation completes;
- both state files identify exact next steps.

Required evidence:

- startup compatibility tests;
- owned-path enforcement test;
- end-to-end fake operation;
- normalized state-file validation.

### VC-00 completion rule

`VC-00` is complete only when `SC-00-001` through `SC-00-010` are `passed` and `DEC-001` is resolved to the degree required by the selected persistence implementation.

## 4. VC-01 success criteria — Framework extraction

### `SC-01-001` — Every supported input has a deterministic outcome

Expected:

Each declared source mode produces either a valid extraction bundle or an explicit machine-readable unavailable/unsupported result.

Required evidence:

Fixture coverage for raw idea, context, TXT, Markdown, PNG, JPEG, HTML, accessible URL, selected feature, local build URL, WebContainer URL, unavailable URL, unauthorized URL, and invalid input.

### `SC-01-002` — Source material and requirement anchors are preserved

Expected:

- original user text/file bytes and hashes are preserved;
- every specified requirement maps to an exact source span or selected visual region;
- ambiguous statements remain unresolved;
- no generated statement is presented as user-specified.

Required evidence:

- source-span tests;
- duplicate attachment test;
- invalid encoding and oversized input tests;
- 100% mapping check for specified requirements.

### `SC-01-003` — Image observation remains provider-neutral

Expected:

- PNG/JPEG intake validates actual media type and orientation;
- provider results validate against the canonical schema;
- provider/version/policy is evidence metadata only;
- image observations are never labeled `measured` unless directly measurable from file geometry;
- provider unavailable and invalid-schema states are explicit.

Required evidence:

- configured-provider fixture;
- unavailable-provider fixture;
- invalid-output fixture;
- provider-field leakage negative test.

Blocking dependency:

`DEC-002` must be resolved before a live provider is enabled outside deterministic fixtures.

### `SC-01-004` — HTML and URL extraction produces measured evidence safely

Expected:

- backend-controlled isolated browser;
- URL scheme and SSRF policy enforced;
- no authentication/CAPTCHA/access bypass;
- visible DOM, roles, computed layout, geometry, viewport, final URL, browser version, console/load evidence captured;
- unauthorized/unavailable pages do not produce inferred layouts.

Required evidence:

- static HTML fixture;
- accessible URL fixture;
- SSRF and local-file escape tests;
- unavailable and unauthorized fixtures;
- browser geometry comparison within fixture tolerance of `±1 CSS px` unless the fixture declares a wider tolerance.

### `SC-01-005` — Structural relationships are correct

Expected:

The system distinguishes at minimum:

- docked versus overlay;
- fixed versus normal flow;
- full-height rail/panel;
- collapsible sidebar;
- resizable split;
- bottom terminal;
- remaining-space region;
- clipping and overflow.

Required evidence:

Paired positive/negative fixtures, including the known docked-inspector versus overlay failure.

### `SC-01-006` — Pattern classification is rule-first and uncertainty-preserving

Expected:

- measured structural rules run before model inference;
- ranked candidates include match reasons and contradictions;
- no primary pattern is assigned below the frozen confidence policy;
- unresolved is returned when evidence cannot distinguish patterns.

Required evidence:

- deterministic classification fixtures;
- ambiguous fixture;
- contradictory-evidence fixture;
- repeated-run byte equality for rule-only outputs.

### `SC-01-007` — No-copy boundary is enforced

Expected:

Canonical output contains no copied logo, brand asset, source color system, font identity, decorative image, source code, or proprietary styling instruction.

Required evidence:

- adversarial branded-source fixtures;
- schema scans proving prohibited fields absent;
- output review showing generic intent labels replace source identity where appropriate.

### `SC-01-008` — Wireframes are deterministic and structurally faithful

Expected:

- Unicode and ASCII outputs preserve major hierarchy, adjacency, nesting, rails, dividers, and resize boundaries;
- repeated compilation from the same contract is byte-identical;
- line integrity tests pass at narrow and wide sizes.

Required evidence:

- snapshot fixtures;
- byte-equality result;
- balanced-corner/intersection validation.

### `SC-01-009` — Builder guidance is completely traceable

Expected:

- every instruction maps to contract IDs;
- component tree, layout, interactions, responsive behavior, accessibility intent, negative guidance, acceptance criteria, and unresolved assumptions are present;
- target adapters change vocabulary only, not behavior;
- prohibited primitives are stated when they contradict the contract.

Required evidence:

- 100% instruction-to-contract mapping check;
- generic, React, and React+shadcn/Radix outputs;
- docked-inspector guidance negative test against Sheet/Drawer substitution.

### `SC-01-010` — Public extraction interfaces are usable by a text-only agent

Expected:

API, CLI, and MCP return concise summaries, uncertainty, evidence classes, stable artifact references, selected-region support, and strict schema behavior without requiring raw image inspection by the consumer.

Required evidence:

- transport tests;
- CLI exit-code tests;
- live MCP unknown-field rejection;
- text-only consumer fixture.

### `SC-01-011` — VC-01 integrates with VC-02 without contract mutation

Expected:

A real VC-01 contract renders through VC-02 fixtures, preserves IDs/evidence links, and requires no shared-schema change.

Required evidence:

- integration operation ID;
- contract and artifact hashes;
- preview result;
- fixture action result;
- both state records referencing the same integration identity.

### VC-01 completion rule

`VC-01` is complete only when `SC-01-001` through `SC-01-011` are `passed`, the accepted `VC-00` freeze identity is unchanged, and live-provider completion does not bypass unresolved `DEC-002`.

## 5. VC-02 success criteria — HTML generation and verification

### `SC-02-001` — A valid contract produces a self-contained preview

Expected:

- supported contract versions only;
- deterministic component IDs and manifest;
- Audisor-owned design tokens;
- no source styling override;
- direct offline review without WebContainer or backend connection.

Required evidence:

- repeated render byte/hash comparison for deterministic artifacts;
- offline browser load;
- protected-token override negative test.

### `SC-02-002` — Generic primitives preserve contract behavior

Expected:

Generated rails, sidebars, docked inspectors, splits, terminal regions, tabs, controls, resize boundaries, and overlays behave according to contract intent. Overlay primitives appear only when explicitly required.

Required evidence:

- component fixture suite;
- stable ID checks;
- semantic HTML/ARIA checks;
- overlay-substitution negative test.

### `SC-02-003` — Layout and interaction states are measurable

Expected:

- open/closed, active/inactive, resize, selected-record preservation, mutual exclusion, and reset are deterministic;
- a docked inspector causes workspace reflow rather than coverage;
- min/default/max dimensions and overflow constraints are enforced.

Required evidence:

- DOM-state and geometry assertions;
- expected layout values within each fixture’s declared tolerance, default `±2 CSS px`;
- replay and reset results.

### `SC-02-004` — Source review is safe and faithful

Expected:

- TXT bytes displayed unchanged;
- Markdown has raw and sanitized views;
- scripts, embedded execution, unsafe URLs, and executable source content are blocked;
- requirement IDs link to source spans;
- represented, unresolved, and unused statements are visible.

Required evidence:

- unsafe Markdown fixture;
- script and URL sanitization tests;
- source hash and span-link checks;
- offline review proof.

### `SC-02-005` — Required review views work offline

Expected:

`Preview`, `Wireframe`, `Components`, `Builder guide`, `Requirements`, `Sources`, and `Evidence` are present, navigable, and usable without a backend connection.

Required evidence:

- offline browser interaction test;
- no required network-request proof;
- tab state and content assertions.

### `SC-02-006` — Existing-layer simulation preserves authority boundaries

Expected:

- neutral target shell;
- selected feature can render standalone and attached;
- before/after layout and released-space behavior are visible;
- fixture data is labeled;
- approval manifest binds contract, preview, tokens, accepted states, and unresolved items;
- unapproved preview is never implementation authority.

Required evidence:

- standalone and generic-shell fixtures;
- approval manifest validation;
- unapproved-reference rejection test.

### `SC-02-007` — WebContainer remains optional

Expected:

The same generated artifact works directly and through a WebContainer adapter when available. WebContainer cannot own canonical state, and client-reported success remains unverified until backend capture.

Required evidence:

- direct-load fixture;
- unavailable-WebContainer fixture;
- successful optional-run fixture;
- state-authority negative test.

### `SC-02-008` — Completed-build capture produces structured evidence

Expected:

For each supported target and frozen viewport, capture:

- final URL and readiness result;
- visible DOM hierarchy;
- roles and accessible names;
- component IDs when available;
- computed layout and geometry;
- scroll, overflow, z-index, and positioning facts;
- console/network errors;
- screenshots/traces;
- before/after interaction state.

Required evidence:

- local target fixture;
- static HTML fixture;
- unavailable target fixture;
- crash/resume capture fixture;
- artifact hashes.

### `SC-02-009` — Responsive claims are limited to tested viewports

Expected:

Compact mobile, tablet, desktop, and 1920×1080 are tested independently. 3840×2160 is tested when the environment declares support. No untested viewport is labeled verified.

Required evidence:

- viewport-specific contracts;
- overflow/clipping results;
- supported/unsupported 4K classification.

### `SC-02-010` — Comparison detects structural and behavioral defects

Expected:

The comparison identifies missing, unexpected, or substituted components; adjacency/containment errors; overlay-versus-flow errors; range violations; state failures; and untestable behavior without treating screenshot similarity as primary proof.

Required evidence:

- intentional overlay defect fixture;
- missing-region fixture;
- resize-bound fixture;
- state-preservation fixture;
- requirement-linked findings with measured evidence.

### `SC-02-011` — Repair guidance identifies root cause

Expected:

Every repair bundle includes:

- failed requirement IDs;
- actual mechanism;
- symptom versus root cause;
- smallest correction;
- behavior to preserve;
- focused test;
- regression checks;
- corrected tree/wireframe when applicable.

Required evidence:

- overlay defect produces normal-flow correction rather than cosmetic repositioning;
- repair bundle schema and evidence-link tests.

### `SC-02-012` — Public preview/capture/compare interfaces are strict

Expected:

API, CLI, and MCP share operation identity, resumable status, artifact references, evidence classifications, failed-criteria IDs, stable exits, and unknown-field rejection.

Required evidence:

- transport tests;
- CLI exit-code tests;
- live MCP unknown-field rejection;
- cross-surface equivalence fixture.

### `SC-02-013` — VC-01 integration round-trips against one contract

Expected:

A real `VC-01` contract renders, replays, captures, and compares back to itself with no required shared-contract amendment. An intentional implementation defect is then detected and produces a repair bundle.

Required evidence:

- one integration operation chain;
- original contract hash;
- preview and capture hashes;
- comparison result;
- repair guide;
- both build states referencing the integration identity.

### `SC-02-014` — Delivery boundary is explicitly accepted

Expected:

`DEC-003` records whether initial VC-02 completion requires:

- standalone artifact plus API/CLI/MCP delivery; or
- integration into the existing `web/` product.

Required evidence:

- accepted decision record;
- delivery validation matching the selected boundary.

### VC-02 completion rule

`VC-02` is complete only when `SC-02-001` through `SC-02-014` are `passed` or legitimately `not_applicable`, the accepted `VC-00` freeze identity is unchanged, and `DEC-003` is resolved.

## 6. Coordinated VC-00 through VC-02 closure criteria

### `SC-X-001` — One end-to-end evidence chain

A raw intent plus selected image or HTML input produces:

- source evidence;
- VC-01 contract;
- deterministic wireframe and guidance;
- VC-02 preview;
- interaction replay;
- structural capture;
- comparison;
- root-cause repair bundle.

All artifacts share one traceable operation/integration identity.

### `SC-X-002` — Intent and source authority remain separate

The locked product requirement defines expected behavior. Source observations support the contract but do not become implementation authority. Generated previews cannot amend the contract.

### `SC-X-003` — Parallel work does not corrupt shared contracts

Concurrent VC-01 and VC-02 fixture runs produce no overlapping writes, contract drift, or fixture drift. Any required amendment blocks both builds and returns to VC-00.

### `SC-X-004` — Failure re-enters at the correct level

- extraction evidence failure returns to VC-01;
- renderer/capture/comparison failure returns to VC-02;
- shared-schema or ownership failure returns to VC-00;
- product-boundary ambiguity returns to the unresolved decision record.

### `SC-X-005` — Final audit proves closure

The final audit verifies:

- all required criteria states;
- scoped diff and protected paths;
- dependency and license evidence;
- no copied source identity;
- no unresolved blocking decision;
- state files and artifact hashes;
- reproducible commands;
- exact remaining limitations.

## 7. Amendment rule

After the first implementation mutation:

1. criteria text and thresholds are immutable under version `SC-VC-00-02-1.0.0`;
2. any correction creates `SC-VC-00-02-1.1.0` or a later version;
3. the amendment records reason, affected IDs, old/new text, approver, and migration effect;
4. an implementation failure cannot be repaired by weakening the criterion without explicit amendment authority.
