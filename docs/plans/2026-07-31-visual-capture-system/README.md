# Audisor Visual Capture System — Coordinated Build Plan

Status: `draft_complete`

Plan ID: `visual-capture-system-2026-07-31`

Repository baseline: `itz1508/theoneshot@bb353ba2d3d1aabd23736743b8eec5ff1d0be11a`

Planning branch: `agent/visual-capture-build-plans`

Expected mutation: `true`

Read-only: `false`

## 1. Objective

Build an Audisor-owned capability that accepts a raw idea, raw context, TXT, Markdown, PNG, JPG/JPEG, HTML, a local or remote webpage URL, or a completed web build and converts the selected interface structure into:

1. a normalized, machine-readable layout contract;
2. an ASCII/Unicode line-drawn wireframe;
3. UI-framework and component-pattern keywords;
4. component-level build guidance;
5. prohibited alternatives and measurable acceptance criteria;
6. a self-contained HTML visualization using Audisor's own design language;
7. a capture-and-compare result after a builder implements the feature;
8. later, a deterministic video and 4K MP4 representation.

The capability must help a user who can recognize a useful interface feature but does not know its technical name or how to describe it to a builder.

## 2. Locked product boundary

### 2.1 What the system extracts

The system extracts only the framework and behavior needed to reproduce a feature in an Audisor-owned version:

- region hierarchy;
- containment;
- horizontal, vertical, grid, split, docked, overlay, fixed, and remaining-space relationships;
- approximate or measured geometry;
- visible component roles;
- component-pattern names and useful technical keywords;
- open, closed, selected, resized, and responsive states;
- interaction intent;
- build constraints;
- acceptance criteria.

### 2.2 What the system must not copy

The system must not reproduce or preserve another site's visual identity or protected expression:

- no copied logos, icons, photographs, illustrations, or brand assets;
- no pixel-perfect copying of colors, typography, decorative effects, or proprietary styling;
- no copied source code or component implementation;
- no copied text beyond the user's own supplied source context;
- no claim that another site's design belongs to Audisor.

The generated HTML must use Audisor's own design tokens and component implementation. The source page is evidence for structure and behavior only.

### 2.3 Research and dependency rule

All earlier external-project research is reference-only. Audisor owns the contracts, schemas, orchestration, pattern vocabulary, wireframe compiler, guidance compiler, HTML compiler, comparison logic, persistence, and recovery behavior.

Replaceable execution runtimes may still be used through narrow adapters when recreating them has no product value, for example:

- a browser engine for rendering and DOM measurement;
- a configured vision provider for image observations;
- FFmpeg for final media encoding.

Those runtimes must not dictate Audisor's core model or folder structure.

## 3. Initial supported inputs

| Input | Initial behavior |
|---|---|
| Raw idea | Convert explicit intent into provisional layout requirements and unresolved decisions. |
| Raw context | Preserve source text and extract only explicit UI requirements. |
| TXT attachment | Preserve unchanged, show in HTML Sources view, and map relevant statements to requirements. |
| Markdown attachment | Preserve unchanged, render safely in Sources view, and map relevant statements to requirements. |
| PNG | Extract selected or whole-page layout observations. |
| JPG/JPEG | Normalize to one image-input path and extract layout observations. |
| HTML file | Render in an isolated browser and collect exact structure and geometry. |
| Webpage URL | Load an accessible page and extract whole-page or selected-feature structure. |
| Local build URL | Capture a running local implementation. |
| WebContainer preview URL | Capture the existing isolated frontend build when available. |
| Completed web build | Exercise required states and compare actual behavior with the approved contract. |

PDF is explicitly out of initial scope. Video input analysis is also out of the first three builds; video generation is Build 03.

## 4. Required outputs

Every successful extraction produces a bundle under one operation ID:

```text
visual-capture/<operation-id>/
├── source-manifest.json
├── sources/
│   ├── original.txt
│   ├── original.md
│   └── original-image-or-html
├── observations.json
├── ui-contract.json
├── pattern-keywords.json
├── component-tree.json
├── wireframe.txt
├── wireframe.json
├── builder-guide.md
├── prohibited-alternatives.json
├── acceptance-criteria.json
├── preview.html
├── comparison.json
├── evidence.json
├── checkpoint.json
└── run-log.jsonl
```

Only applicable artifacts are required for a given operation. Missing optional artifacts must be reported as `not_applicable`, not silently omitted.

## 5. Build decomposition

Four build IDs provide the best success and recovery boundary.

| Build ID | Name | Purpose | Dependency |
|---|---|---|---|
| `VC-00` | Backend foundation | Freeze contracts, operation state, artifact storage, adapter ports, APIs, recovery, and fixtures. | None |
| `VC-01` | Framework extraction | Convert idea/text/image/HTML/URL into normalized layout evidence, pattern names, wireframes, and builder guidance. | `VC-00` contract freeze |
| `VC-02` | HTML compiler and reproduction verifier | Generate Audisor-owned HTML, attach TXT/MD sources, simulate integration over an existing layer, capture completed builds, and compare expected versus actual. | `VC-00` contract freeze; may use fixtures before `VC-01` integration |
| `VC-03` | Video renderer | Convert an approved interaction timeline into deterministic frames and validated MP4/4K artifacts. | Stable `VC-02` capture timeline and HTML output |

## 6. Execution graph

```text
VC-00 Backend foundation
        │
        ├───────────────┐
        ▼               ▼
VC-01 Extraction   VC-02 HTML compiler
        │               │
        └───────┬───────┘
                ▼
       Integration checkpoint
                │
                ▼
       VC-03 Video renderer
```

### 6.1 Parallel-run rule

`VC-01` and `VC-02` may run at the same time only after `VC-00` freezes:

- contract version;
- schema files;
- operation and artifact interfaces;
- fixture contract examples;
- error vocabulary;
- evidence vocabulary.

`VC-01` owns extractor and pattern-compiler paths. `VC-02` owns HTML-renderer, preview, capture, and comparison paths. Neither build may edit the frozen shared schemas during parallel work.

When a shared-contract change is required:

1. both parallel builds stop at a checkpoint;
2. one `VC-00` amendment is proposed;
3. the amendment receives a new contract version;
4. fixtures are regenerated;
5. both builds resume against the same version.

### 6.2 Video scheduling rule

`VC-03` must not begin production integration at the same time as the first extraction/HTML builds. It may prototype against frozen fixtures only after `VC-00`, but final integration waits until `VC-02` proves a stable interaction timeline and deterministic browser capture.

## 7. Proposed repository ownership

The implementation agent must confirm these paths against active repository ownership before mutation. The intended boundary is:

```text
audisor_visual/
├── pyproject.toml
├── src/audisor_visual/
│   ├── contracts/
│   ├── operations/
│   ├── artifacts/
│   ├── adapters/
│   ├── extractors/
│   ├── patterns/
│   ├── wireframe/
│   ├── guidance/
│   ├── html/
│   ├── capture/
│   ├── comparison/
│   ├── api/
│   ├── cli/
│   └── mcp/
└── tests/

web/
└── src/visual-capture/

audisor_media/
├── pyproject.toml
├── src/audisor_media/
│   ├── timeline/
│   ├── frames/
│   ├── encoding/
│   └── validation/
└── tests/
```

The preferred design is a new standalone `audisor_visual` package rather than placing the capability inside the tombstoned legacy runtime or coupling it to the OneShot Fix engine. The implementation preflight must verify that no newer active owner already exists.

## 8. Shared contract rules

The canonical contract is not HTML and not ASCII. It is a versioned UI contract from which all outputs are generated.

Required top-level concepts:

- `SourceManifest`;
- `SelectionScope`;
- `LayoutObservation`;
- `Region`;
- `Relationship`;
- `Geometry`;
- `ComponentPattern`;
- `ComponentIntent`;
- `Interaction`;
- `StateTransition`;
- `ResponsiveRule`;
- `EvidenceItem`;
- `BuilderInstruction`;
- `AcceptanceCriterion`;
- `OperationCheckpoint`.

Required evidence states:

- `specified`;
- `observed`;
- `measured`;
- `inferred`;
- `verified`;
- `unresolved`;
- `unavailable`;
- `not_applicable`.

Image-provider guesses remain `inferred`. Browser geometry is `measured`. Behavior becomes `verified` only after the system performs the interaction and observes the expected state transition.

## 9. Recovery and continuation model

Every build must be recoverable after agent interruption, provider failure, machine restart, or usage-limit exhaustion.

Each build maintains:

```text
docs/plans/2026-07-31-visual-capture-system/state/
├── VC-00.json
├── VC-01.json
├── VC-02.json
└── VC-03.json
```

Each state record contains:

- build ID;
- plan version;
- contract version;
- repository branch and HEAD;
- current phase, task, and step;
- completed steps;
- active target paths;
- pre-mutation hashes;
- last successful command and exit code;
- validation status;
- artifact IDs;
- unresolved issues;
- next executable step;
- rollback instructions;
- timestamp.

State rules:

1. Update the build state after every completed step that changes repository or artifact state.
2. Append, never rewrite, run events in `run-log.jsonl`.
3. Never mark a phase complete until its validation checkpoint passes.
4. Record failed attempts and preserve their artifacts.
5. A restored agent must read the plan, state file, run log, literal repository status, and current diff before continuing.
6. A restored agent must not repeat a non-idempotent step without proving it did not already complete.
7. Phase commits and pushes require the repository's normal explicit authority; state persistence does not silently authorize publishing.

## 10. Common phase lifecycle

Every build follows the same phase structure:

1. `preflight`
2. `contract_and_fixture_review`
3. `proof_first_tests`
4. `implementation`
5. `targeted_validation`
6. `integration_validation`
7. `diff_and_license_review`
8. `checkpoint_and_handoff`

Each task inside a phase must state:

- objective;
- exact owned paths;
- excluded paths;
- prerequisites;
- ordered steps;
- expected evidence;
- failure classification;
- rollback boundary;
- completion criteria.

## 11. Cross-build invariants

1. No source design is copied.
2. The source URL or image is never treated as implementation authority.
3. TXT and Markdown are preserved as source attachments but only mapped requirements control output.
4. HTML output uses Audisor design tokens.
5. URL capture records the URL, viewport, time, and access outcome.
6. Authenticated pages require an explicitly supplied authorized session.
7. CAPTCHA, permission denial, robots restrictions, or unreachable pages return `unavailable`; the system does not guess.
8. WebContainer remains an optional isolated frontend runner, not the canonical evidence authority.
9. Backend-controlled browser capture provides measured evidence.
10. Video artifacts never replace structural or behavioral verification.
11. No PDF support is added during these builds.
12. No heavy OCR/document-understanding subsystem is introduced.
13. Unknown fields in public MCP schemas are rejected.
14. External provider output is validated before entering the canonical contract.
15. Every generated instruction maps to source evidence, user specification, or a clearly labeled inference.

## 12. Shared acceptance criteria

The coordinated system is successful when:

1. A raw sentence describing a right-side review rail produces a valid contract, pattern keywords, wireframe, component tree, builder guide, prohibited alternatives, and acceptance criteria.
2. A selected PNG/JPEG region produces framework-only observations without copying visual assets.
3. An HTML file produces measured DOM/CSS/geometry observations.
4. An accessible webpage URL produces whole-page or selected-feature layout output.
5. A TXT or Markdown attachment appears unchanged in the generated HTML Sources view and maps relevant statements to requirement IDs.
6. A text-only AI can consume the MCP/CLI/API output without receiving raw pixels.
7. The HTML compiler renders an Audisor-owned preview from the contract.
8. The existing-layer simulation shows how a selected feature fits into a generic target shell.
9. The verifier detects a docked-inspector requirement implemented incorrectly as an overlay drawer.
10. The completed-build capture records DOM, accessibility, geometry, screenshots, console state, and interaction results.
11. Two agents can run `VC-01` and `VC-02` concurrently without editing the same owned paths or changing the frozen contract.
12. A stopped build can resume from its state record without repeating completed non-idempotent work.
13. `VC-03` renders a deterministic timeline into validated 1080p and 3840×2160 MP4 artifacts.

## 13. Build documents

- [`BUILD-00-backend-foundation.md`](BUILD-00-backend-foundation.md)
- [`BUILD-01-framework-extraction.md`](BUILD-01-framework-extraction.md)
- [`BUILD-02-html-generation-and-verification.md`](BUILD-02-html-generation-and-verification.md)
- [`BUILD-03-video-renderer.md`](BUILD-03-video-renderer.md)
- [`state-template.json`](state-template.json)

## 14. Implementation authorization boundary

This document is a completed implementation plan, not implementation authorization by itself. Before code mutation, the implementing agent must:

1. read repository authority files and applicable skills;
2. record branch, HEAD, literal dirty state, submodule state, and exact target paths;
3. verify active ownership and protected surfaces;
4. submit the completed plan through the repository's current A-Flow artifact lifecycle when that tool is available;
5. act on the review result;
6. obtain any required path, commit, push, dependency, or external-tool authorization.

## 15. Plan-detection summary

```json
{
  "plan_id": "visual-capture-system-2026-07-31",
  "source_kind": "plan",
  "expects_mutation": true,
  "read_only": false,
  "steps": [
    {"action_id": "VC-00", "objective": "Build and freeze the shared backend foundation"},
    {"action_id": "VC-01", "objective": "Build the framework extraction and guidance compiler"},
    {"action_id": "VC-02", "objective": "Build the HTML compiler, source viewer, capture, and verifier"},
    {"action_id": "VC-03", "objective": "Build the deterministic video renderer"}
  ]
}
```
