# VC-01 — Framework Extraction and Builder Guidance

Status: `planned`

Build ID: `VC-01`

Depends on: completed `VC-00` contract freeze

May run concurrently with: `VC-02`

Must not modify: frozen shared contracts, HTML-renderer ownership, video paths

## Goal

Convert an idea, text attachment, screenshot, HTML file, or accessible webpage URL into framework-only interface knowledge that a text-only AI or UI builder can use.

The build must identify what the useful feature is called, how its regions relate, which components are needed, how it behaves, what must not be built, and how to verify the implementation. It must not reproduce the source site's visual design.

## Expected outputs

- `observations.json`;
- `ui-contract.json`;
- `pattern-keywords.json`;
- `component-tree.json`;
- `wireframe.txt`;
- `wireframe.json`;
- `builder-guide.md`;
- `prohibited-alternatives.json`;
- `acceptance-criteria.json`;
- evidence and unresolved-assumption records.

## Supported source modes

1. `raw_idea`
2. `raw_context`
3. `text_attachment`
4. `markdown_attachment`
5. `image_whole`
6. `image_selected_region`
7. `html_file`
8. `url_whole_page`
9. `url_selected_feature`
10. `running_build_url`
11. `webcontainer_preview_url`

PDF and video input remain unsupported.

# Phase 0 — Build preflight

## Task 0.1 — Reattach to VC-00

### Steps

1. Read the master plan and `VC-01` state.
2. Verify the repository branch, HEAD, status, and target paths.
3. Verify the frozen contract version and fixture hashes.
4. Verify no `VC-02` path overlaps the extractor paths.
5. Verify fake adapter conformance tests still pass.
6. Record the exact implementation paths owned by this build.
7. Stop if the shared contract differs from the state record.

### Completion

The build is attached to one immutable contract version and has exclusive path ownership.

## Task 0.2 — Freeze extraction non-goals

### Steps

1. Record that the objective is layout and behavior, not document reading.
2. Record that source styling and assets are not copied.
3. Record that labels may be collected only to identify component purpose.
4. Record that image observations may be approximate.
5. Record that URL/HTML measurements may be exact for the captured viewport only.
6. Record that authenticated sources require explicit authorized access.

# Phase 1 — Text and idea extraction

## Task 1.1 — Preserve source attachments

### Steps

1. Accept raw strings, `.txt`, and `.md` files.
2. Preserve original bytes and SHA-256.
3. Detect encoding safely.
4. Reject unsupported binary content.
5. Store original attachment metadata.
6. Do not normalize away user wording.
7. Create source-reference IDs for later requirement mapping.

### Tests

- UTF-8 TXT;
- UTF-8 Markdown;
- empty file;
- oversized file;
- invalid encoding;
- duplicate attachment;
- path traversal filename.

## Task 1.2 — Extract explicit requirements

### Steps

1. Segment source statements without rewriting the original.
2. Identify explicit regions, positions, sizes, states, actions, constraints, and prohibited alternatives.
3. Mark each extracted statement `specified`.
4. Preserve ambiguous statements as unresolved instead of guessing.
5. Map every requirement to its source span.
6. Separate visual requirements from product/business context.
7. Produce a provisional component-intent graph.

### Example

Source:

```text
Add a small full-height column on the right. Clicking Snapshot opens record details beside it, not over the workspace.
```

Required extraction:

- persistent right utility rail;
- snapshot rail item;
- reusable docked inspector;
- normal-flow layout;
- main workspace reflow;
- prohibited: overlay sheet, modal, off-canvas drawer.

## Task 1.3 — Raw-idea inference

### Steps

1. Accept informal or incomplete descriptions.
2. Identify candidate patterns.
3. Rank candidates by confidence.
4. Mark non-explicit behavior as `inferred`.
5. Generate unresolved decisions when candidates conflict.
6. Never silently choose a destructive or structurally different pattern.
7. Produce builder guidance only from accepted or clearly labeled inferred requirements.

### Completion

Text input produces a valid provisional UI contract without requiring image or browser support.

# Phase 2 — Image layout observation

## Task 2.1 — Image intake

### Steps

1. Accept PNG and JPG/JPEG.
2. Detect actual media type.
3. Normalize orientation while preserving the original.
4. Record width, height, pixel density when available, and source hash.
5. Accept whole-image or selected-region scope.
6. Expand selected-region context by a bounded margin for relationship analysis.
7. Reject malformed, oversized, or unsupported images deterministically.

## Task 2.2 — Provider-neutral image observer

### Steps

1. Implement the `ImageLayoutObserver` port from `VC-00`.
2. Define a strict structured request for regions, boundaries, relationships, component roles, and likely interactions.
3. Add one configured provider adapter without exposing provider fields in the core contract.
4. Validate all provider output against the observation schema.
5. Record model/provider/version and request policy in evidence.
6. Mark provider-only results `inferred` or `observed`, never `measured`.
7. Return `provider_unavailable` when no vision provider is configured.
8. Allow a text-only AI to request and consume the stored structured result.

## Task 2.3 — Geometry normalization

### Steps

1. Normalize provider boxes into source-pixel coordinates.
2. Detect obvious parent/child containment.
3. Detect left/right/top/bottom ordering.
4. Detect probable rows, columns, rails, panels, and split regions.
5. Record approximate proportions.
6. Preserve contradictory observations.
7. Never infer exact pixels from an image unless supplied by the source metadata and directly measurable.

## Task 2.4 — Framework-only filtering

### Steps

1. Remove brand identity from output.
2. Exclude copied colors, font identities, logos, photographs, and decorative assets.
3. Replace source-specific labels with generic component intent when needed.
4. Preserve only labels required to explain interaction meaning.
5. Add an evidence note explaining that generated HTML will use Audisor styling.
6. Add tests proving image-specific brand fields do not enter the UI contract.

### Completion

A selected image feature produces structural observations and useful technical keywords without a copycat visual specification.

# Phase 3 — HTML and URL extraction

## Task 3.1 — Browser runtime adapter

### Steps

1. Implement a replaceable backend-controlled browser adapter.
2. Support local HTML files, data copied into an operation workspace, and accessible URLs.
3. Use a fixed browser/version policy for deterministic fixtures.
4. Apply URL scheme and network policy.
5. Block unsupported local-file escape and SSRF targets.
6. Record navigation outcome, final URL, viewport, timestamp, and browser version.
7. Capture failure as `source_unavailable` or `source_unauthorized`, not an inferred layout.

## Task 3.2 — Static HTML file extraction

### Steps

1. Load the supplied HTML in isolation.
2. Disable or restrict unsafe external execution according to policy.
3. Wait for an explicit readiness rule.
4. Collect visible DOM nodes.
5. Collect semantic roles and accessible names where present.
6. Collect computed display, position, overflow, flex, grid, and visibility properties.
7. Collect bounding rectangles.
8. Collect viewport and document dimensions.
9. Map browser facts to `measured` observations.
10. Preserve console and load errors as evidence.

## Task 3.3 — Webpage URL extraction

### Steps

1. Validate the URL and access policy.
2. Load the page without attempting to bypass authentication, CAPTCHA, consent, or access restrictions.
3. Support whole-page and selected-feature scope.
4. Allow an authorized session adapter when explicitly configured.
5. Capture the same DOM, role, CSS, and geometry evidence as static HTML.
6. Record if the page changes after initial render.
7. Capture only the selected feature plus bounded structural context when selected mode is used.
8. Never persist cookies, credentials, or secret headers in public artifacts.

## Task 3.4 — Layout relationship derivation

### Steps

1. Derive normal-flow versus overlay behavior.
2. Derive docked versus fixed-position panels.
3. Derive parent flex and grid relationships.
4. Derive which region receives remaining space.
5. Detect overflow and clipping.
6. Detect full-height rails and panels.
7. Detect obvious collapsible controls from attributes and before/after evidence when available.
8. Record viewport-specific truth separately from responsive inference.

## Task 3.5 — Selected-feature extraction

### Steps

1. Accept a selector, DOM target, coordinates, or user-picked region.
2. Resolve the selected element and meaningful ancestors.
3. Include adjacent regions needed to explain layout effects.
4. Exclude unrelated page content.
5. Produce a compact feature contract.
6. Record unresolved behavior that cannot be proven from one static state.

### Completion

Any accessible HTML page can be converted to framework and component guidance without copying its presentation.

# Phase 4 — Pattern and keyword compiler

## Task 4.1 — Initial pattern vocabulary

Implement deterministic definitions for:

1. persistent utility rail;
2. docked inspector;
3. collapsible sidebar;
4. resizable split panel;
5. bottom terminal panel;
6. master-detail view;
7. tabbed workspace;
8. accordion;
9. inline expandable detail;
10. modal dialog;
11. overlay sheet;
12. off-canvas drawer;
13. command palette;
14. toolbar;
15. activity rail;
16. responsive mobile drawer.

## Task 4.2 — Rule-first classification

### Steps

1. Match measured structural rules before model inference.
2. Use component roles, position, containment, and layout effects.
3. Return ranked candidates.
4. Include why each candidate matched.
5. Include contradictory evidence.
6. Require a minimum confidence before assigning one primary name.
7. Return `unresolved` when the structure cannot distinguish patterns.

## Task 4.3 — Keyword generation

### Steps

1. Generate framework-neutral keywords.
2. Generate behavior keywords.
3. Generate optional target-specific vocabulary without changing behavior.
4. Include useful negative keywords such as `not overlay`, `not modal`, and `normal layout flow`.
5. Keep keywords mapped to contract IDs.

### Example

```text
persistent right utility rail
reusable docked inspector
normal-flow panel
workspace reflow
edge-mounted toggle
preserve selected record
single active inspector host
not overlay sheet
not off-canvas drawer
```

# Phase 5 — Component tree and state model

## Task 5.1 — Component hierarchy

### Steps

1. Convert regions and pattern results into a generic component tree.
2. Separate host shell, navigation, active content, inspector host, and individual inspector content.
3. Add stable component IDs.
4. Record required and optional components.
5. Record placement and sizing constraints.
6. Avoid framework-specific implementation names in the canonical tree.

## Task 5.2 — Interaction model

### Steps

1. Define triggers.
2. Define state transitions.
3. Define layout effects.
4. Define preserved state.
5. Define mutually exclusive states.
6. Define responsive substitutions.
7. Mark unverified interactions separately.

## Task 5.3 — Accessibility intent

### Steps

1. Define button and tab semantics.
2. Define expanded/collapsed state reporting.
3. Define focus behavior.
4. Define keyboard expectations.
5. Define labels for generic generated components.
6. Avoid claiming source-page accessibility compliance without proof.

# Phase 6 — ASCII/Unicode wireframe compiler

## Task 6.1 — Deterministic grid projection

### Steps

1. Convert region hierarchy into rows and columns.
2. Allocate cells proportionally within configured limits.
3. Preserve major nesting and adjacency.
4. Render box-drawing characters.
5. Label regions with generic component intent.
6. Show toggles, rails, dividers, and resize boundaries.
7. Produce plain ASCII fallback when Unicode is unavailable.
8. Emit a matching machine-readable `wireframe.json`.

## Task 6.2 — Line integrity

### Steps

1. Validate balanced corners and intersections.
2. Validate labels fit or truncate predictably.
3. Validate nested layouts.
4. Validate narrow and wide output sizes.
5. Snapshot-test canonical fixtures.
6. Prove repeated compilation is byte-identical.

# Phase 7 — Builder guidance compiler

## Task 7.1 — Buildable guidance

### Required sections

1. Feature name.
2. Purpose.
3. Framework-neutral component tree.
4. Layout relationships.
5. Dimensions and constraints.
6. Interactions and state transitions.
7. Responsive behavior.
8. Accessibility intent.
9. Source-versus-inference table.
10. Implementation sequence.
11. Prohibited alternatives.
12. Acceptance criteria.
13. Unresolved assumptions.

### Steps

1. Generate each statement from contract IDs.
2. Never add unsupported visual design details.
3. Explain normal-flow, docked, overlay, or responsive behavior explicitly.
4. Add builder-friendly technical keywords.
5. Include a concise copy-ready instruction section.
6. Include exact tests the builder must run or demonstrate.

## Task 7.2 — Target vocabulary adapters

Initial target vocabularies:

- generic HTML/CSS;
- React;
- React + shadcn/Radix.

### Steps

1. Translate component vocabulary only.
2. Preserve canonical behavior.
3. Avoid forcing a Card, Sheet, Drawer, or other primitive when it contradicts the contract.
4. Explain when a primitive must not be used.
5. Test docked-inspector guidance against the known overlay failure case.

# Phase 8 — Public interfaces

## Task 8.1 — API operation

### Candidate action

```text
POST /v1/visual/extractions
```

### Steps

1. Accept one source and one scope.
2. Start or resume through the operation store.
3. Return artifact references.
4. Support requested output subsets.
5. Validate strict schemas.
6. Add bounded status polling.

## Task 8.2 — CLI

### Candidate commands

```text
audisor-visual extract idea
audisor-visual extract text
audisor-visual extract image
audisor-visual extract html
audisor-visual extract url
```

### Steps

1. Support JSON and human-readable output.
2. Support selected-region metadata.
3. Support artifact export.
4. Add deterministic exit codes.

## Task 8.3 — MCP tools for text-only AI

### Candidate tools

```text
audisor_visual_extract_idea
audisor_visual_extract_image
audisor_visual_extract_html
audisor_visual_extract_url
audisor_visual_get_contract
audisor_visual_get_builder_guide
```

### Steps

1. Return concise summaries and structured artifact references.
2. Never require the consuming AI to inspect raw image bytes.
3. Permit follow-up by component ID.
4. Expose uncertainty and evidence classes.
5. Prove unknown-field rejection through live MCP transport.

# Phase 9 — Validation and handoff

## Task 9.1 — Required fixtures

1. Raw snapshot-inspector idea.
2. TXT with mixed product and UI context.
3. Markdown with explicit prohibited overlay behavior.
4. PNG selected feature.
5. JPEG whole interface.
6. HTML with docked panel.
7. HTML with fixed overlay panel.
8. URL whole page.
9. URL selected feature.
10. URL unavailable.
11. Provider unavailable.
12. Provider invalid schema.
13. Narrow Unicode rendering.
14. Component keyword translation.
15. Text-only MCP consumer.

## Task 9.2 — Extraction correctness

### Steps

1. Verify source preservation.
2. Verify evidence classification.
3. Verify no visual-identity fields enter the contract.
4. Verify HTML measurements match browser geometry.
5. Verify image dimensions remain approximate.
6. Verify docked versus overlay classification.
7. Verify deterministic wireframe output.
8. Verify every builder instruction maps to evidence.

## Task 9.3 — Parallel integration handoff

### Steps

1. Export canonical contracts for `VC-02`.
2. Compare emitted contracts with the fixtures used by `VC-02`.
3. Resolve version differences through a controlled `VC-00` amendment only.
4. Run extraction → HTML generation integration fixtures.
5. Record final artifact hashes.
6. Update `state/VC-01.json` with completion and `VC-02` integration identity.

# Completion criteria

`VC-01` is complete only when:

1. every supported input mode produces a valid result or explicit unavailable classification;
2. URL and HTML extraction produce measured layout facts;
3. image extraction produces structured, provider-neutral observations;
4. source design assets and styling are excluded;
5. the system identifies and distinguishes docked inspector, overlay drawer, collapsible sidebar, and terminal split patterns;
6. wireframe output is deterministic;
7. the builder guide includes component structure, behavior, negative guidance, and acceptance criteria;
8. a text-only AI can retrieve and reason from the result;
9. integration with `VC-02` succeeds without changing the frozen contract;
10. recovery state points to the exact next step or records terminal completion.

# Stop conditions

Stop and preserve evidence when:

- the source cannot legally or technically be accessed;
- an authenticated session was not explicitly authorized;
- provider output does not validate;
- the source does not contain enough evidence to distinguish patterns;
- a contract change is required during parallel execution;
- another build has modified an owned path;
- the implementation begins copying visual identity instead of extracting framework.
