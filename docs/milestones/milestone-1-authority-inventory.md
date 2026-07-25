# Milestone 1 — Authority Reconciliation and Migration Inventory

- Source plan: `milestone-1-authority-inventory-r2`
  (LF-normalized SHA-256 `5633757646d6fad82782ddd2e503800da175308f6869531310197056a54215bc`)
- Baseline HEAD: `25767c4eee658a7fa790de74908d10ec60cb9b11` (branch `main`)
- Submodule: `audisor` @ `6d38e7967271f108bcbcf7dc3c1fae83607e37f4`, unchanged
- Verified: 2026-07-25, by direct filesystem/repository inspection
- Purpose: persist the verified authority-reconciliation inventory as tracked
  documentation so later milestones (runtime lifecycle migration, controller
  completion) execute against an agreed, evidence-backed baseline instead of
  re-deriving it. This milestone is documentation-only; it authorizes no
  migration.

## Section 1 — `audisor.audisor_lifecycle` consumer matrix (verified 2026-07-25)

| Consumer | Import sites | Classification |
|---|---|---|
| Codex PreToolUse hook | `.codex/hooks.json` → `python -m audisor.audisor_lifecycle.hook` (runs from `openai_project/runtime`) | Active, governance-critical |
| Fix backend host | `audisor_backend/controllers/fix_host.py` L14–17: `artifacts`, `analysis_package`, `ignition`, `operation` | Active, production Fix path |
| Fix backend tests | `audisor_backend/tests/fix/test_fix_host.py`, `test_fix_host_routing.py` (11 sites) | Test-only; pins contract |
| Runtime-internal | `audisor_run_gate.py`, `aflow_mcp_server.py`, `codex/analysis_request.py`, `builder/executor.py`, `integrate.py`, `execution_launcher.py` | Mixed; `builder/executor.py` legacy BuildExecutor; others require per-module classification in the migration milestone |
| Operation Controller | Zero imports (`controller.py` L143: "Does not import from audisor.* directly") | Clean; target-state pattern |

## Section 2 — Package and repository boundaries

- Runtime dist `audisor` 0.10.0 (`openai_project/runtime`) self-describes as
  "tombstoned in 0.10.0; /health and /ready only" yet ships the 14-module
  `audisor_lifecycle` package that the hook and Fix backend depend on. This
  is the core contradiction later milestones must resolve.
- Toolkit submodule dist is `audisor-local` 0.9.0; it does **not** satisfy
  `audisor>=0.9.0` and is not on the Fix backend resolution path. Boundary
  clean: toolkit changes remain separate commits/releases with submodule
  pointer updates only after toolkit validation evidence.
- OneShot Fix image (`packaging/oneshot-fix/Dockerfile`) installs the
  tombstoned runtime first; the backend's `audisor>=0.9.0` requirement
  resolves to it. Deployment resolution confirmed.
- A-Flow identity: **unresolved collision, no winner chosen.** Two packages
  expose the same distribution name `theoneshot-aflow` and the same console
  script `aflow`:
  - `D:\Dev\Aflow_cli` — `theoneshot-aflow` 0.1.0, external working tree
    with no committed baseline (every source file untracked; not
    reproducibly versioned). For the Milestone 1 operation it served only
    as the plan-review surface; that role confers no product authority.
  - `openai_project/aflow` — `theoneshot-aflow` 0.9.0, tracked in this
    repository; the tracked `README.md` identifies it as the Active A-Flow
    product.
  The `.codex/contract-matrix.json` claim that the external tree is the
  "canonical A-Flow product" is unsettled authority contradicted by the
  tracked `README.md`; this inventory records the collision as fact and
  defers resolution to a later milestone.

## Section 3 — Delivered older-plan work (status corrections)

- Operation Controller exists at `openai_project/operation_controller/`;
  the `.codex/contract-matrix.json` verdict "DOES NOT EXIST" is stale and
  superseded by this inventory. (File correction deferred: protected surface.)
- OperationEnvelope, Fix routing, fulfilment coordinator, per-slice
  authority, container entrypoint, and release-version changes are delivered
  in current source at the baseline.
- Controller acceptance coverage stops at `sandbox_ready`; execution,
  authoritative validation, Apply, and replay remain incomplete.

## Section 4 — Verified migration candidate register (NOT authorised here)

| Candidate | Evidence | Affected paths | Prerequisites |
|---|---|---|---|
| Retarget PreToolUse hook off tombstoned runtime | `.codex/hooks.json` L9–10 | `.codex/hooks.json`, replacement lifecycle module | Migration milestone plan naming `.codex/` + explicit human approval; wire-level regression tests for the hook protocol (fail-closed structured deny) |
| Repair stale `audisor_backend/uv.lock` | Lock lists only `pydantic`; `pyproject.toml` declares `audisor>=0.9.0` — lock/manifest contradiction | `audisor_backend/uv.lock`, possibly `packaging/oneshot-fix/backend-requirements.txt` | Milestone plan naming `packaging/`; decision on how `audisor` resolves in local uv context (path dependency vs. index) |
| Migrate `fix_host.py` lifecycle imports | `audisor_backend/controllers/fix_host.py` L14–17 | `audisor_backend/**`, new lifecycle ownership surface | Import/caller matrix (this milestone); compatibility adapters; one behavior at a time with regression tests |
| Repair or retire `runtime.aflow_submit_plan` bridge | Nine deterministic schema-admission findings from the Revision 1 submission; bridge builds a request missing all eight required v1 fields | `openai_project/runtime/src/audisor/aflow_mcp_server.py` (+ transport regression test covering `aflow_submit_plan`, not only the low-level tool) | Milestone plan naming `openai_project/runtime/`; live-transport proof of admission per MCP strictness policy |
| Correct stale `.codex/contract-matrix.json` controller verdict | `agent_gateway_verdict` says "DOES NOT EXIST"; `openai_project/operation_controller/` exists at baseline | `.codex/contract-matrix.json` | Milestone plan naming `.codex/` + explicit human approval |
| Establish committed baseline for `Aflow_cli` | Substantial implementation with zero commits; not reproducibly versioned | `D:\Dev\Aflow_cli` (separate repository) | Separate-repository decision: init + initial commit + version/tag discipline; out of Theoneshot mutation scope |

## Review and disposition history (Milestone 1)

Recorded outside Sections 1–4; Sections 1–4 above reproduce the reviewed plan
content, with one correction mandated at implementation time (the Section 2
A-Flow identity entry no longer repeats the "canonical" label; it records the
unresolved collision instead).

- Revision 1 (`milestone-1-authority-inventory`, digest `4ba46df8…4fa6`):
  submitted once via the runtime `aflow_submit_plan` bridge; outcome
  `transport_envelope_not_valid`, `plan_content_evaluated: false` (nine
  deterministic schema-admission findings against the bridge-generated
  envelope). Operation preserved `blocked`, never finalised, discarded, or
  resumed. Evidence: `analysis-decision.json` SHA-256
  `02ebf48f42761cd94965fa028c915ba9e4619bd74a9022d27b5a4955effc4343`.
- Revision 2 (`milestone-1-authority-inventory-r2`, digest `5633…15bc`):
  submitted once via the external `aflow_review` surface; outcome
  `review_processed_with_unresolved_gaps` with proof the plan text was
  evaluated (11 stages; steps derived from actual plan sections). Sole
  unresolved gap `semantic-observation-001` (local semantic-review timeout;
  deterministic review completed) accepted by human disposition as
  non-blocking uncertainty for this documentation-only milestone.
- Review-surface defects observed (candidates for the external repository's
  own backlog, not this register): the review's three "resolved" material
  gaps were keyword-triggered verifier-template false positives citing plan
  sections that do not exist in the plan, and the generated validation
  specs/fixtures execute only `print(...)` statements. The generated
  companion is quarantined as review history with no implementation
  authority; the three appended feature contracts (CLI operation-ID
  resolution, path-traversal policy, post-verification resume behavior) are
  explicitly rejected as product requirements.
- Human-authority disposition `disposition-milestone-1-r2` (local record,
  untracked by design) overrode the plan's unsatisfiable gate wording and
  authorized exactly this one tracked file.
