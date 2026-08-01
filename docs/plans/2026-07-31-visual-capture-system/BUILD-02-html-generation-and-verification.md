# VC-02 — HTML Generation, Existing-Layer Simulation, and Reproduction Verification

Status: `planned`

Build ID: `VC-02`

Depends on: completed `VC-00` contract freeze

May run concurrently with: `VC-01`

Final integration depends on: `VC-01` emitted contracts

Must not modify: frozen shared contracts, extractor ownership, video paths

## Goal

Generate a self-contained Audisor-owned HTML visualization from the canonical UI contract, preserve TXT and Markdown sources inside the review surface, simulate how an extracted feature fits over an existing application layer, and verify whether a builder's completed implementation reproduces the required structure and behavior.

The generated HTML is a fast approval and acceptance surface. It is not copied source design and is not the canonical contract.

## Expected outputs

- `preview.html`;
- `preview-manifest.json`;
- `sources-manifest.json`;
- `interaction-fixtures.json`;
- `capture-manifest.json`;
- `actual-ui-contract.json`;
- `comparison.json`;
- `repair-guide.md`;
- screenshots and browser evidence;
- checkpoint and run logs.

## Rendering modes

1. `standalone_feature`
2. `generic_application_shell`
3. `existing_layer_simulation`
4. `fixture_interactive`
5. `capture_target`
6. `comparison_review`

# Phase 0 — Build preflight

## Task 0.1 — Reattach to VC-00

### Steps

1. Read the master plan and `VC-02` state.
2. Verify branch, HEAD, status, and exact target paths.
3. Verify the frozen contract version and fixture hashes.
4. Verify no `VC-01` path overlaps HTML and capture paths.
5. Verify fixture contracts can be loaded without extractor implementation.
6. Verify the current `web/` application architecture and existing WebContainer integration.
7. Record whether the HTML compiler belongs in `audisor_visual`, `web`, or both through an adapter boundary.
8. Stop if the UI host or execution owner is unresolved.

## Task 0.2 — Freeze rendering rules

### Steps

1. Record that the contract is authoritative.
2. Record that generated HTML uses Audisor design tokens.
3. Record that source colors, fonts, icons, images, and decorative styling are not reproduced.
4. Record that source TXT/MD is displayed as context, not executed as layout code.
5. Record that all simulated data is labeled `fixture`.
6. Record that WebContainer is optional and not backend authority.
7. Record the browser/version policy for deterministic capture.

# Phase 1 — HTML compiler foundation

## Task 1.1 — Contract loader

### Steps

1. Load only supported contract versions.
2. Validate the contract before rendering.
3. Resolve component IDs, requirement IDs, and evidence references.
4. Reject unresolved required regions when the selected mode requires them.
5. Preserve optional and unresolved items in a review panel.
6. Add deterministic fixture tests.

## Task 1.2 — Audisor design-token layer

### Steps

1. Define a compact token set for background, surfaces, borders, text, status, spacing, type, and corners.
2. Keep tokens independent of source-page styles.
3. Support light/dark only if already required by the product; do not expand scope otherwise.
4. Store token version in the preview manifest.
5. Add tests proving source style fields cannot override protected Audisor tokens.

## Task 1.3 — Generic component primitives

Initial primitives:

- application shell;
- activity or utility rail;
- sidebar;
- docked inspector;
- split region;
- terminal region;
- tabs;
- toolbar;
- list;
- detail panel;
- collapsible boundary;
- resizable divider;
- status bar;
- modal and overlay primitives only when explicitly required by the contract.

### Steps

1. Render from generic component intent.
2. Keep component behavior deterministic.
3. Add stable `data-audisor-component-id` attributes.
4. Add requirement and evidence IDs as nonvisual metadata.
5. Use semantic HTML and ARIA states.
6. Avoid framework coupling in the generated artifact.

# Phase 2 — Layout and interaction generation

## Task 2.1 — Layout compiler

### Steps

1. Convert horizontal and vertical relationships to CSS layout.
2. Convert grid relationships when explicitly required.
3. Implement remaining-space sizing.
4. Implement normal-flow docked panels.
5. Implement overlay behavior only when explicitly required.
6. Implement full-height rails.
7. Implement fixed minimum, default, and maximum dimensions.
8. Prevent unexpected page overflow.
9. Add viewport-specific responsive rules.
10. Record every layout decision in the preview manifest.

## Task 2.2 — State model runtime

### Steps

1. Generate open and closed states.
2. Generate active and inactive rail items.
3. Generate selected-record state.
4. Generate resize state.
5. Generate preserved previous width.
6. Generate mutually exclusive inspector content.
7. Generate fixture reset.
8. Keep state local and deterministic.
9. Add accessible expanded/selected state reporting.

## Task 2.3 — Interaction fixtures

### Steps

1. Convert contract interactions into fixture actions.
2. Assign stable action IDs.
3. Define starting state.
4. Define user action.
5. Define expected DOM state.
6. Define expected geometry change.
7. Define expected preserved state.
8. Define failure evidence.
9. Add reset and replay support.

### Example

```text
Action: click snapshot rail item
Expected:
- snapshot inspector becomes visible;
- inspector is between main workspace and rail;
- main workspace width decreases;
- inspector does not cover the workspace;
- selected snapshot remains after collapse and reopen.
```

# Phase 3 — Source attachment review surface

## Task 3.1 — TXT viewer

### Steps

1. Embed or package the original TXT safely.
2. Show filename, hash, encoding, and source status.
3. Display original content unchanged.
4. Link extracted requirement IDs to source spans.
5. Show represented, unresolved, and unused statements.
6. Never execute source content.

## Task 3.2 — Markdown viewer

### Steps

1. Preserve original Markdown bytes.
2. Render a sanitized preview.
3. Provide a raw-source view.
4. Disable embedded scripts and unsafe URLs.
5. Link requirements to source headings or line spans.
6. Show which statements affected the rendered feature.

## Task 3.3 — Review tabs

The generated HTML review surface must include:

```text
Preview | Wireframe | Components | Builder guide | Requirements | Sources | Evidence
```

### Steps

1. Keep the Preview as the default tab.
2. Display the deterministic wireframe.
3. Display the generic component tree.
4. Display builder guidance and prohibited alternatives.
5. Display requirement-to-component mapping.
6. Display source attachments.
7. Display evidence and uncertainty.
8. Ensure all tabs work without a backend connection.

# Phase 4 — Existing-layer simulation

## Task 4.1 — Generic target shell

### Steps

1. Generate a neutral application shell representing the target's main regions.
2. Do not copy source-page styling.
3. Preserve only measured or specified structural dimensions.
4. Label the shell as a simulation.
5. Support selected-feature insertion.

## Task 4.2 — Feature integration modes

### Steps

1. Render the feature standalone.
2. Render it attached to the generic target shell.
3. Render closed, open, resized, and selected states.
4. Show before-and-after layout.
5. Show how released space returns to the main workspace.
6. Show overlay warnings when an implementation would obscure content.
7. Allow the user to approve one state set as the acceptance reference.

## Task 4.3 — Approval record

### Steps

1. Record contract hash.
2. Record preview hash.
3. Record token version.
4. Record approved fixture states.
5. Record unresolved items.
6. Freeze an immutable approval manifest.
7. Never treat an unapproved preview as implementation authority.

# Phase 5 — WebContainer integration

## Task 5.1 — Preserve execution boundary

### Steps

1. Inspect the existing WebContainer integration before changing it.
2. Keep generated HTML usable without WebContainer.
3. Use WebContainer only to run an actual frontend project or isolated fixture build.
4. Do not store canonical operation state inside WebContainer.
5. Do not treat client-reported success as backend verification.
6. Record WebContainer outputs as `client_reported` until independently captured.

## Task 5.2 — Preview runner adapter

### Steps

1. Mount a generated fixture project when WebContainer is available.
2. Start the project through its supported command.
3. wait for a server-ready event.
4. return the preview URL to the backend capture runner.
5. capture terminal output and exit status.
6. classify unavailable WebContainer separately from failed generated HTML.
7. preserve a local/Docker/browser path so the product is not dependent on WebContainer.

### Completion

The same generated HTML can be reviewed directly and run in WebContainer without changing its contract.

# Phase 6 — Completed-build capture

## Task 6.1 — Capture target intake

### Supported targets

- local build URL;
- WebContainer preview URL;
- accessible deployed URL;
- generated static HTML.

### Steps

1. Validate access and source policy.
2. Freeze viewport and browser version.
3. load the target.
4. wait for explicit readiness.
5. record final URL and load result.
6. preserve console and network errors.
7. stop rather than guess when the target is unavailable.

## Task 6.2 — Structural capture

### Steps

1. Collect visible DOM hierarchy.
2. Collect semantic roles and accessible names.
3. Collect component ID metadata when present.
4. Collect computed layout properties.
5. Collect bounding rectangles.
6. Collect scroll and overflow state.
7. collect z-index and positioning facts needed to detect overlay behavior.
8. Map measured results to `actual-ui-contract.json`.

## Task 6.3 — Interaction capture

### Steps

1. Replay approved fixture actions.
2. Capture state before each action.
3. perform the action.
4. capture state after the action.
5. verify expected DOM changes.
6. verify expected geometry changes.
7. verify preserved state.
8. save screenshots and trace references.
9. record one result per action ID.

## Task 6.4 — Responsive capture

Initial required viewports:

- compact mobile;
- tablet;
- desktop;
- 1920×1080;
- 3840×2160 when supported.

### Steps

1. Run required viewports independently.
2. capture layout facts.
3. detect clipping and overflow.
4. detect incorrect overlay substitution.
5. store viewport-specific actual contracts.
6. avoid claiming responsiveness outside tested viewports.

# Phase 7 — Comparison engine

## Task 7.1 — Structural comparison

Compare:

- required regions;
- parent/child hierarchy;
- component roles;
- labels needed for interaction;
- active/inactive state;
- open/closed state;
- expected controls;
- accessibility state.

### Steps

1. Match by stable IDs when available.
2. fall back to role and relationship matching.
3. distinguish missing, unexpected, and substituted components.
4. produce requirement-linked findings.
5. classify severity.

## Task 7.2 — Geometric comparison

Compare:

- position;
- adjacency;
- containment;
- width/height ranges;
- main-workspace reflow;
- overlay versus normal flow;
- resize limits;
- full-height behavior;
- overflow.

### Steps

1. Normalize viewport coordinates.
2. apply contract tolerances.
3. compare expected and actual relationships before raw pixels.
4. confirm docked-versus-overlay behavior from geometry and z-order.
5. generate exact measured evidence.

## Task 7.3 — Behavioral comparison

### Steps

1. Compare expected and observed action results.
2. verify collapse/expand.
3. verify state preservation.
4. verify active inspector replacement.
5. verify main-workspace reflow.
6. verify responsive substitution.
7. record untestable behavior as `unavailable`, not passed.

## Task 7.4 — Visual review boundary

### Steps

1. Use screenshot comparison only as supplemental evidence.
2. ignore expected differences caused by Audisor styling.
3. focus visual review on missing regions, overlap, clipping, and hierarchy.
4. never require pixel identity with the source site.
5. preserve deterministic structural findings as primary evidence.

# Phase 8 — Repair guidance

## Task 8.1 — Root-cause-oriented findings

### Steps

1. Identify the violated contract requirement.
2. identify the actual implementation mechanism when measurable.
3. distinguish symptom from structural cause.
4. describe the smallest correction.
5. list protected behavior that must remain.
6. add a focused acceptance test.

### Example

```text
Expected: docked inspector participates in horizontal layout.
Actual: panel uses position: fixed and covers the workspace.
Root cause: overlay positioning removes the panel from normal flow.
Correction: place InspectorHost between MainWorkspace and UtilityRail as a flex item; remove fixed positioning; make MainWorkspace flex: 1 and min-width: 0.
```

## Task 8.2 — Builder-ready repair bundle

Generate:

- failed requirement IDs;
- measured evidence;
- corrected component tree;
- corrected wireframe;
- implementation steps;
- prohibited alternatives;
- focused tests;
- regression checks.

# Phase 9 — Public interfaces

## Task 9.1 — API

Candidate actions:

```text
POST /v1/visual/previews
POST /v1/visual/captures
POST /v1/visual/comparisons
GET  /v1/visual/previews/{id}
GET  /v1/visual/comparisons/{id}
```

### Steps

1. operate through the shared operation store.
2. return artifact references.
3. support resumable capture.
4. expose evidence classification.
5. enforce strict schemas.

## Task 9.2 — CLI

Candidate commands:

```text
audisor-visual preview
audisor-visual capture
audisor-visual compare
audisor-visual repair-guide
```

### Steps

1. support contract files and operation IDs.
2. support local target URLs.
3. export self-contained bundles.
4. provide stable exit codes.

## Task 9.3 — MCP

Candidate tools:

```text
audisor_visual_generate_preview
audisor_visual_capture_build
audisor_visual_compare_build
audisor_visual_get_repair_guide
```

### Steps

1. return structured summaries for text-only agents.
2. keep screenshots behind artifact references.
3. reject unknown fields through live transport.
4. expose exact failed criteria.

# Phase 10 — Validation and integration

## Task 10.1 — Required fixtures

1. Standalone docked inspector.
2. Generic-shell docked inspector.
3. Incorrect overlay implementation.
4. Collapsible Explorer.
5. Bottom terminal resize.
6. TXT source viewer.
7. Markdown source viewer with unsafe HTML.
8. Fixture-state replay.
9. WebContainer unavailable.
10. WebContainer successful preview.
11. Local browser capture.
12. Responsive overflow.
13. 4K capture.
14. Crash during capture.
15. Resume after artifact write.

## Task 10.2 — Determinism

### Steps

1. render the same contract repeatedly.
2. compare preview manifest bytes.
3. compare component IDs.
4. compare interaction fixture bytes.
5. compare structural capture under frozen browser conditions.
6. document permitted screenshot variance.

## Task 10.3 — VC-01 integration

### Steps

1. consume real `VC-01` contracts.
2. compare them with fixture contracts.
3. render previews.
4. run fixture interactions.
5. capture generated output.
6. compare generated output back to the same contract.
7. resolve only implementation defects; shared-contract amendments return to `VC-00`.
8. record integration hashes in both build states.

# Completion criteria

`VC-02` is complete only when:

1. a valid contract produces a self-contained Audisor-styled HTML review surface;
2. TXT and Markdown sources are preserved and safely viewable;
3. the preview contains Preview, Wireframe, Components, Builder guide, Requirements, Sources, and Evidence views;
4. standalone and existing-layer simulations work;
5. fixture actions prove open, close, resize, selected-state preservation, and workspace reflow;
6. generated HTML works without WebContainer;
7. WebContainer can run it as an optional isolated frontend path;
8. completed-build capture produces DOM, accessibility, geometry, console, screenshot, and interaction evidence;
9. comparison detects docked-versus-overlay errors;
10. repair guidance identifies structural root cause and focused correction;
11. integration with `VC-01` succeeds without changing the frozen contract;
12. recovery state can resume interrupted capture and comparison work.

# Stop conditions

Stop and preserve evidence when:

- the implementation starts copying source visual identity;
- the UI host or capture authority is unresolved;
- WebContainer is becoming the canonical backend state owner;
- source attachments can execute unsafe content;
- a contract change is required during parallel work;
- the browser target is unavailable or unauthorized;
- structural verification is being replaced by pixel similarity;
- another build modifies an owned path.
