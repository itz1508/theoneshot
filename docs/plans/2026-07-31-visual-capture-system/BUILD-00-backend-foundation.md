# VC-00 — Backend Foundation

Status: `planned`

Build ID: `VC-00`

Depends on: none

Blocks: `VC-01`, `VC-02`, `VC-03`

## Goal

Create the standalone Audisor Visual backend foundation and freeze the contracts that allow extraction and HTML generation to proceed concurrently without sharing mutable implementation paths.

## Expected result

A tested backend package exists with:

- versioned visual-capture schemas;
- operation and artifact persistence;
- adapter interfaces;
- fixture contracts;
- API, CLI, and MCP surfaces;
- explicit evidence and error vocabulary;
- resumable build state;
- no image, HTML, or video implementation yet beyond deterministic fakes.

## Excluded

- no image interpretation;
- no URL capture;
- no HTML generation;
- no browser automation;
- no video or FFmpeg integration;
- no WebContainer mutation;
- no legacy runtime revival;
- no changes to protected `.codex`, `Agents.md`, or skill files.

# Phase 0 — Repository preflight

## Task 0.1 — Resolve active ownership

### Steps

1. Read all applicable authority files and matching skills.
2. Record branch, HEAD, literal status including untracked files, and submodule state.
3. Inspect whether a current visual-capture package or operation owner already exists.
4. Inspect `audisor_backend`, `web`, active MCP registration, artifact stores, and operation-controller surfaces.
5. Confirm that the legacy runtime is not the implementation host.
6. Confirm whether the preferred new package path `audisor_visual/` conflicts with packaging or repository policy.
7. Record exact target and excluded paths in the build state.
8. Stop if ownership is ambiguous.

### Evidence

- repository discovery record;
- active-path map;
- literal status;
- target-path hash inventory;
- ownership decision.

### Completion

One active package owner and one operation owner are proven.

## Task 0.2 — Establish recovery boundary

### Steps

1. Follow repository snapshot requirements before build action.
2. Record the snapshot or approved rollback mechanism.
3. Initialize `state/VC-00.json` from the shared template.
4. Create an append-only operation log location.
5. Record the first executable step.

### Completion

A restored agent can determine the baseline without relying on chat history.

# Phase 1 — Package skeleton

## Task 1.1 — Create the standalone package

### Candidate paths

```text
audisor_visual/
├── pyproject.toml
├── README.md
├── src/audisor_visual/
│   ├── __init__.py
│   ├── version.py
│   ├── errors.py
│   └── types.py
└── tests/
```

The implementation preflight may adjust these paths only when active ownership proves a different host.

### Steps

1. Add the minimal Python 3.11+ package.
2. Keep dependencies limited to already-approved foundations where possible.
3. Add package version and contract version separately.
4. Add a package README that states the no-copy design boundary.
5. Add import and packaging smoke tests.
6. Verify clean editable and non-editable imports.

### Completion

The package installs and imports without pulling browser, vision, or video dependencies.

## Task 1.2 — Define error vocabulary

### Required classifications

- `invalid_input`;
- `unsupported_input`;
- `source_unavailable`;
- `source_unauthorized`;
- `capture_failed`;
- `provider_unavailable`;
- `provider_contract_invalid`;
- `contract_invalid`;
- `operation_conflict`;
- `artifact_write_failed`;
- `verification_failed`;
- `dependency_unavailable`;
- `canceled`;
- `internal_error`.

### Steps

1. Define stable machine codes.
2. Separate safe public messages from internal evidence.
3. Add retryable and terminal classifications.
4. Add tests proving no unknown code is emitted.

# Phase 2 — Canonical contracts

## Task 2.1 — Source and scope schemas

### Define

- `SourceManifest`;
- `SourceAttachment`;
- `SourceKind`;
- `SelectionScope`;
- `Viewport`;
- `SourceProvenance`.

### Steps

1. Support raw idea, raw context, TXT, Markdown, PNG, JPG/JPEG, HTML, URL, local build URL, and WebContainer preview URL.
2. Exclude PDF and video input.
3. Normalize JPG/JPEG media types.
4. Require SHA-256 for file-backed sources.
5. Preserve original source metadata without making it implementation authority.
6. Validate URLs and deny unsupported schemes.
7. Add fixtures for valid and invalid sources.

## Task 2.2 — Layout observation schema

### Define

- regions;
- relationships;
- geometry;
- component-role observations;
- text labels as optional evidence, not the extraction objective;
- confidence;
- evidence state;
- source references.

### Steps

1. Represent horizontal, vertical, grid, docked, overlay, fixed, sticky, and remaining-space relationships.
2. Distinguish approximate from measured dimensions.
3. Represent selected-feature context separately from whole-page layout.
4. Permit unresolved observations without inventing values.
5. Reject provider-specific fields from the core schema.
6. Add round-trip and unknown-field tests.

## Task 2.3 — UI contract schema

### Define

- pattern name and keywords;
- component tree;
- component intent;
- layout constraints;
- interactions;
- state transitions;
- responsive rules;
- prohibited alternatives;
- acceptance criteria;
- source/evidence mapping.

### Steps

1. Keep the contract independent of React, shadcn, or another target framework.
2. Give every component and requirement a stable ID.
3. Require every generated instruction to reference evidence or user specification.
4. Represent Audisor design-token ownership separately from source observations.
5. Add schema versioning and migration policy.
6. Create fixtures for the right utility rail, docked inspector, collapsible explorer, and bottom terminal.

## Task 2.4 — Operation and checkpoint schemas

### Define

- operation identity;
- build identity;
- contract version;
- phase/task/step;
- status;
- attempts;
- artifact references;
- validation results;
- next step;
- rollback data.

### Steps

1. Make state transitions explicit and validated.
2. Make terminal completion idempotent.
3. Prevent completed steps from silently running twice.
4. Preserve failed attempts.
5. Add crash-recovery fixtures.

# Phase 3 — Persistence and orchestration

## Task 3.1 — Operation store

### Steps

1. Add atomic operation creation.
2. Add idempotency keys.
3. Add append-only attempt records.
4. Add compare-and-swap or equivalent state-version protection.
5. Add resume lookup by operation ID.
6. Prove concurrent updates cannot erase newer state.
7. Add cancellation and terminal-state rules.

## Task 3.2 — Artifact store

### Steps

1. Store artifacts under operation-scoped paths.
2. Hash every artifact.
3. Store media type, producer, contract version, and evidence classification.
4. Write atomically.
5. Preserve failed outputs separately from accepted outputs.
6. Reject path traversal.
7. Add artifact listing and retrieval.
8. Prove repeated finalization does not duplicate accepted artifacts.

## Task 3.3 — Orchestrator

### Steps

1. Accept an operation request.
2. Freeze source and policy metadata.
3. Invoke adapters only through defined ports.
4. Validate adapter output before persistence.
5. Persist stage result before advancing.
6. Stop on unresolved or unavailable required evidence.
7. Expose resumable next-step state.
8. Add deterministic fake adapters for all ports.

# Phase 4 — Ports and public surfaces

## Task 4.1 — Adapter ports

### Required ports

- `TextRequirementExtractor`;
- `ImageLayoutObserver`;
- `HtmlLayoutObserver`;
- `UrlLayoutObserver`;
- `PatternCompiler`;
- `WireframeCompiler`;
- `GuidanceCompiler`;
- `HtmlPreviewCompiler`;
- `BuildCaptureRunner`;
- `ComparisonEngine`;
- `VideoRenderer`.

### Steps

1. Define typed request and response contracts.
2. Require capability reporting.
3. Require deterministic unavailable responses.
4. Prevent adapters from persisting directly.
5. Prevent adapters from mutating shared contracts.
6. Add fake adapters and conformance tests.

## Task 4.2 — API

### Candidate endpoints

```text
POST /v1/visual/operations
GET  /v1/visual/operations/{operation_id}
POST /v1/visual/operations/{operation_id}/resume
GET  /v1/visual/operations/{operation_id}/artifacts
GET  /v1/visual/capabilities
```

### Steps

1. Use strict request schemas.
2. Reject unknown properties.
3. Return operation IDs before long-running work.
4. Return no unverified claim as completed.
5. Add transport-level tests.

## Task 4.3 — CLI

### Candidate commands

```text
audisor-visual capabilities
audisor-visual start
audisor-visual status
audisor-visual resume
audisor-visual artifacts
```

### Steps

1. Provide JSON output mode.
2. Preserve exit-code semantics.
3. Support operation resume.
4. Add CLI contract tests.

## Task 4.4 — MCP

### Candidate tools

```text
audisor_visual_capabilities
audisor_visual_start
audisor_visual_status
audisor_visual_resume
audisor_visual_get_artifact
```

### Steps

1. Set `additionalProperties: false`.
2. Reject unknown fields through live transport tests.
3. Return structured evidence consumable by text-only agents.
4. Keep raw binary artifacts behind artifact references.
5. Avoid registering implementation-specific tools before their builds exist.

# Phase 5 — Fixture freeze and parallel handoff

## Task 5.1 — Shared fixture corpus

### Required fixtures

1. Right utility rail with docked inspector.
2. Incorrect right overlay drawer.
3. Left collapsible Explorer.
4. Main screen with bottom terminal.
5. Raw idea describing a snapshot record detail panel.
6. Markdown requirements attachment.
7. TXT notes attachment.
8. Static HTML shell.
9. URL capture unavailable.
10. Invalid provider output.
11. Crash after artifact persistence but before state advance.
12. Concurrent resume attempt.

### Steps

1. Create canonical input fixtures.
2. Create expected observation contracts.
3. Create expected UI contracts.
4. Create expected wireframes and guidance summaries.
5. Hash and freeze fixtures.
6. Publish contract version `1.0.0` only after all fixture tests pass.

## Task 5.2 — Parallel ownership handoff

### Steps

1. Record the frozen contract commit.
2. Record exact paths owned by `VC-01`.
3. Record exact paths owned by `VC-02`.
4. Prohibit shared-schema edits by both builds.
5. Initialize `VC-01.json` and `VC-02.json` with the same contract version.
6. Run a fake-adapter end-to-end operation.
7. Confirm both builds can run their fixture suites independently.

# Validation

## Targeted

- schema validation;
- operation transition tests;
- atomic artifact tests;
- adapter conformance;
- API strictness;
- CLI exit codes;
- live MCP unknown-field rejection.

## Integration

- fake source → fake observation → validated contract → persisted artifacts;
- crash and resume;
- concurrent resume conflict;
- idempotent terminal finalization;
- artifact hash verification;
- package clean-install proof.

## Completion criteria

`VC-00` is complete only when:

1. the package owner is proven;
2. contract `1.0.0` is frozen;
3. fixture hashes are recorded;
4. operation and artifact stores pass concurrency and recovery tests;
5. API, CLI, and MCP base surfaces pass strict-schema tests;
6. no browser, vision, HTML, or video implementation has leaked into the foundation;
7. `VC-01` and `VC-02` can start from separate owned paths using the same frozen fixtures;
8. the recovery state identifies the exact next step for each build.

# Stop conditions

Stop and report instead of mutating when:

- an active visual owner already exists elsewhere;
- the proposed package conflicts with repository packaging;
- protected paths would need mutation;
- the working tree contains conflicting target changes;
- the operation store owner is unresolved;
- public schema strictness cannot be proven;
- contract changes are still occurring while parallel builds are scheduled.
