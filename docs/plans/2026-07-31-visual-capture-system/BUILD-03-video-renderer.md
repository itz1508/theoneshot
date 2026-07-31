# VC-03 — Deterministic Video and 4K Renderer

Status: `planned`

Build ID: `VC-03`

Depends on:

- completed `VC-00` contracts and operation persistence;
- stable `VC-02` HTML output, interaction fixtures, and capture timeline.

May prototype from frozen fixtures after `VC-00`, but final integration must not begin before `VC-02` is stable.

## Goal

Generate reproducible demonstration and evidence videos from an approved UI contract and interaction timeline. Produce validated MP4 artifacts at 1080p and 3840×2160 without making video the source of truth for structure or behavior.

This is the heavy subsystem and must remain isolated from the thin visual-capture core.

## Expected outputs

- `timeline.json`;
- `render-plan.json`;
- `frame-manifest.json`;
- deterministic PNG frame sequences or resumable frame chunks;
- `evidence-recording` when browser recording is enabled;
- `presentation-1080p.mp4`;
- `presentation-4k.mp4`;
- poster image and thumbnail;
- media validation report;
- encoder capability and license manifest;
- checkpoint and run logs.

## Product boundary

- Audisor owns the timeline, frame plan, overlays, captions, artifact identity, validation, and recovery.
- FFmpeg or another encoder is an external process adapter, not copied source or architecture.
- The video does not prove that the underlying application is correct; it consumes verified `VC-02` states.
- Edited presentation clips remain separate from raw execution evidence.

# Phase 0 — Build preflight

## Task 0.1 — Verify prerequisites

### Steps

1. Read the master plan and `VC-03` state.
2. Verify branch, HEAD, status, and target paths.
3. Verify the frozen contract version.
4. Verify `VC-02` approved preview hash and interaction timeline identity.
5. Verify the browser capture environment.
6. Detect FFmpeg/ffprobe availability without installing or changing tools.
7. Record encoder version, build configuration, and available codecs.
8. Verify output-storage capacity and configured limits.
9. Stop if the required timeline or renderer authority is unresolved.

## Task 0.2 — Establish media isolation

### Preferred package boundary

```text
audisor_media/
├── pyproject.toml
├── src/audisor_media/
│   ├── timeline/
│   ├── frames/
│   ├── overlays/
│   ├── encoding/
│   ├── validation/
│   └── operations/
└── tests/
```

### Steps

1. Confirm the package path does not conflict with existing media ownership.
2. Keep heavy media dependencies out of `audisor_visual` base installation.
3. Communicate through versioned contracts and artifact references.
4. Prevent the media package from mutating UI contracts.
5. Record exact owned paths.

# Phase 1 — Timeline contract

## Task 1.1 — Define timeline entities

### Required entities

- scene;
- state reference;
- action reference;
- start time;
- duration;
- viewport;
- cursor path;
- focus target;
- caption;
- callout;
- transition;
- audio reference when later supported;
- output profile.

### Steps

1. Build on `VC-00` contract versioning.
2. Reference `VC-02` component and action IDs.
3. Reject unknown component or action IDs.
4. Require a deterministic start state.
5. Require bounded duration.
6. Define frame rate explicitly.
7. Define output resolution explicitly.
8. Add schema and migration tests.

## Task 1.2 — Compile interaction fixtures into scenes

### Steps

1. Read approved fixture actions.
2. order actions.
3. insert deterministic pauses.
4. define cursor start and destination.
5. define captions from approved builder guidance or explicit user text.
6. avoid inventing product claims.
7. define final state.
8. produce one canonical timeline.

## Task 1.3 — Timeline validation

### Steps

1. Verify every state exists.
2. verify every action is replayable.
3. verify total duration and frame count.
4. detect overlapping incompatible scenes.
5. detect missing assets.
6. detect unsupported resolution or frame-rate combinations.
7. hash the accepted timeline.

# Phase 2 — Deterministic render environment

## Task 2.1 — Freeze environment identity

### Steps

1. Record operating system/container identity.
2. record browser version.
3. record viewport and device scale factor.
4. record font inventory and hashes.
5. disable animations unless the timeline explicitly owns them.
6. disable caret blinking and other nondeterministic UI.
7. freeze locale and timezone.
8. freeze fixture data and clocks.
9. record preview and contract hashes.

## Task 2.2 — Resolution profiles

Initial profiles:

- `1920×1080` at configured frame rate;
- `3840×2160` at configured frame rate;
- optional compact derivative generated from the same approved source.

### Steps

1. Validate dimensions.
2. validate browser support.
3. validate memory and storage limits.
4. define pixel format.
5. define color-space policy.
6. define scaling only for derivatives, never as a substitute for native 4K rendering.

# Phase 3 — Frame renderer

## Task 3.1 — Scene execution

### Steps

1. Load the approved HTML or completed verified build.
2. reset to the scene start state.
3. wait for deterministic readiness.
4. execute the referenced action at the defined time.
5. capture state evidence.
6. capture frames at the configured rate.
7. stop and preserve the partial segment on failure.
8. record scene completion atomically.

## Task 3.2 — Cursor and focus overlays

### Steps

1. Generate an Audisor-owned cursor overlay.
2. map cursor positions to measured target geometry.
3. interpolate paths deterministically.
4. render click indication.
5. render focus emphasis when requested.
6. keep overlays separate from application evidence.
7. record overlay parameters in the frame manifest.

## Task 3.3 — Captions and callouts

### Steps

1. Use explicit or approved text only.
2. render with bundled or approved fonts.
3. maintain safe margins.
4. avoid covering the demonstrated component.
5. add accessibility transcript output.
6. record exact text and timing.
7. provide a no-caption evidence render when required.

## Task 3.4 — Frame chunking and resume

### Steps

1. Divide rendering into scene or fixed-size frame chunks.
2. write each chunk atomically.
3. hash every frame or chunk.
4. persist the highest contiguous completed frame.
5. on resume, validate existing hashes.
6. rerender only missing or invalid chunks.
7. prevent duplicate frame numbering.
8. preserve failed chunks for diagnosis.

# Phase 4 — Encoder adapter

## Task 4.1 — Capability probe

### Steps

1. invoke `ffmpeg -version` and `ffmpeg -buildconf` through an isolated process adapter.
2. invoke `ffprobe` capability checks.
3. record version and configuration.
4. detect required encoders.
5. classify missing or policy-disallowed encoders.
6. do not mutate system installation.
7. store the capability result with the operation.

## Task 4.2 — Safe command construction

### Steps

1. build argument arrays, not shell command strings.
2. reject user-controlled arbitrary flags.
3. restrict input and output paths to operation storage.
4. apply duration, resource, and output-size limits.
5. capture stdout and stderr with size bounds.
6. terminate and reap timed-out processes.
7. record the exact safe argument vector.
8. redact secrets if later audio or remote sources are introduced.

## Task 4.3 — Encode MP4

### Steps

1. validate the complete frame manifest.
2. encode the 4K primary artifact.
3. encode the 1080p derivative from approved frames or a separately rendered profile according to policy.
4. use an approved codec and pixel format.
5. enable fast-start when appropriate.
6. generate poster and thumbnail.
7. persist artifacts atomically.
8. hash all media outputs.
9. never mark complete before media validation.

# Phase 5 — Evidence and presentation separation

## Task 5.1 — Raw evidence recording

### Steps

1. optionally retain the browser's raw recording or trace-linked capture.
2. record that it is unedited execution evidence.
3. preserve its environment identity.
4. keep it immutable.
5. link it to the verified action results.

## Task 5.2 — Presentation clip

### Steps

1. render cursor, captions, and callouts from the timeline.
2. identify it as an edited presentation artifact.
3. link every scene to source state and action IDs.
4. prevent edited content from replacing the evidence result.
5. produce a transcript and scene list.

# Phase 6 — Media validation

## Task 6.1 — Container and stream validation

### Steps

1. run ffprobe on each output.
2. verify container type.
3. verify video codec.
4. verify width and height.
5. verify frame rate.
6. verify duration tolerance.
7. verify frame count when available.
8. verify pixel format and color metadata.
9. verify fast-start or expected stream placement.
10. reject truncated or unreadable output.

## Task 6.2 — Frame-content validation

### Steps

1. sample required frames from every scene.
2. verify the expected state reference.
3. verify no blank or error page.
4. verify captions and callouts remain inside safe bounds.
5. verify cursor targets the intended component.
6. verify no unexpected clipping at 4K.
7. compare scene boundary hashes when deterministic.

## Task 6.3 — Artifact identity

### Steps

1. store timeline hash.
2. store preview/build hash.
3. store frame-manifest hash.
4. store encoder identity.
5. store media hash.
6. store validation result.
7. make repeated finalization idempotent.

# Phase 7 — Public interfaces

## Task 7.1 — API

Candidate actions:

```text
POST /v1/visual/videos
GET  /v1/visual/videos/{operation_id}
POST /v1/visual/videos/{operation_id}/resume
GET  /v1/visual/videos/{operation_id}/artifacts
```

### Steps

1. accept timeline or approved operation references.
2. operate asynchronously through persisted operation state without claiming background completion outside the actual runtime.
3. expose explicit progress and checkpoint identity.
4. enforce strict schemas.
5. return artifact references only after validation.

## Task 7.2 — CLI

Candidate commands:

```text
audisor-media render
audisor-media status
audisor-media resume
audisor-media validate
```

### Steps

1. support JSON progress.
2. support deterministic resume.
3. expose capability probe results.
4. provide stable exit codes.

## Task 7.3 — MCP

Candidate tools:

```text
audisor_visual_render_video
audisor_visual_video_status
audisor_visual_resume_video
audisor_visual_get_video_artifact
```

### Steps

1. return text-only progress and evidence.
2. keep video bytes behind artifact references.
3. reject unknown fields through live transport.
4. expose partial, failed, and validated states accurately.

# Phase 8 — Required fixtures

## Task 8.1 — Fixture set

1. Two-scene docked inspector demonstration.
2. Collapse and reopen with preserved record.
3. Resize interaction.
4. Bottom terminal open/close.
5. Caption-safe-bound test.
6. Cursor-target geometry test.
7. 1080p render.
8. 4K render.
9. Missing FFmpeg.
10. Missing encoder.
11. FFmpeg timeout.
12. Oversized stderr.
13. Crash after frame chunk.
14. Resume with one corrupt chunk.
15. Truncated MP4.
16. Invalid resolution.
17. Repeated finalization.
18. Evidence-versus-presentation separation.

# Phase 9 — Validation and release handoff

## Task 9.1 — Targeted validation

- timeline schema tests;
- deterministic frame-plan tests;
- chunk resume tests;
- process timeout/reap tests;
- command-injection tests;
- ffprobe validation tests;
- operation idempotency tests.

## Task 9.2 — Integrated validation

### Steps

1. take an approved `VC-02` fixture.
2. compile the timeline.
3. render frames.
4. encode 1080p.
5. encode 4K.
6. validate both.
7. sample scene frames.
8. verify transcript and scene mapping.
9. crash and resume one render.
10. compare artifact hashes when environment is frozen.

## Task 9.3 — Dependency and license evidence

### Steps

1. record external executable identities.
2. record distribution and deployment assumptions.
3. generate dependency/license manifest.
4. verify no copied source has entered Audisor-owned folders.
5. stop release when encoder policy is unresolved.

# Completion criteria

`VC-03` is complete only when:

1. an approved `VC-02` timeline renders without modifying the UI contract;
2. rendering is chunked and resumable;
3. 1920×1080 and 3840×2160 outputs validate;
4. the encoder runs through a bounded external-process adapter;
5. failed or timed-out encoder processes are terminated and reaped;
6. raw evidence and edited presentation artifacts remain separate;
7. the frame and media manifests preserve complete provenance;
8. a text-only AI can request status and retrieve artifact references;
9. repeated finalization is idempotent;
10. the recovery state identifies the exact next frame, scene, or validation step.

# Stop conditions

Stop and preserve evidence when:

- `VC-02` timeline or preview identity changes;
- the required encoder is unavailable or policy-disallowed;
- storage or resource limits would be exceeded;
- a process cannot be confirmed terminated;
- frame hashes disagree during resume;
- the renderer begins using video as substitute proof for application correctness;
- an external package architecture or source implementation is being copied.
