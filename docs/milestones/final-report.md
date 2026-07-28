# Final report — A-Flow lifecycle maintainability refactor

**Implementation result**: complete

**Current behavioural validation**: passed

**Historical source comparison**: not performed; original builder-side source was not retained

The refactor of the canonical A-Flow artifact lifecycle is complete and
behaviourally validated. The size policy is enforced end to end, and the
26-fixture validation suite passes. The current implementation is
internally consistent and behaviourally validated. Tests verify the
lifecycle contracts and scenarios they cover.

Exact textual continuity with the unretained pre-refactor source—particularly
prompt wording, source representation, and any untested private details—was
not independently compared.

## Change budget

- Production files added / modified: 10 new capability modules + 1
  orchestrator rewrite + 1 `__init__.py` adjustment = **12 production
  files** (≤25 budget respected).
- Test files added / modified: 10 split lifecycle test modules +
  `flow_fixtures.py` + `manifest_builder.py` +
  `test_compatibility_manifest.py` + `test_public_import_smoke.py` +
  `test_fixture_policy_discovery.py` + `run_fixture_validation.py` =
  **15 test files** (≤20 budget respected).
- Package boundaries touched: `audisor.audisor_lifecycle` (one
  package, internal layout change only). ≤3 boundary budget respected.
- **No existing runtime modules were removed.** The four legacy
  lifecycle integration modules (`active_state.py`, `hook.py`,
  `plan_trigger.py`, `review_contract.py`) and their two test modules
  were initially removed in error during the refactor and have been
  restored. They are separate lifecycle integration surfaces (Codex
  hook evaluator, A-Flow plan bridge, review→lock wrapper, active-state
  envelope writer) — not part of the artifact_flow decomposition.

## Gate summary

| Gate | Status | Evidence |
|---|---|---|
| 1 — repo comprehension | complete | git state, configs, callers, size inventory captured |
| 1 — baseline tests | complete | full runtime suite green before any mutation |
| Budget | complete | ≤25 / ≤20 / ≤3 budget declared and respected |
| 2 — compatibility manifest | complete | `tests/audisor_lifecycle/manifest_builder.py` + stored fixture; manifest validates current contract consistency; historical source comparison was not performed |
| 3 — size policy | complete | `docs/code-size-policy.md` is the single authoritative policy; validation integration via fixture runner (see Validation integration section) |
| 4 — size checker | complete | `scripts/check_size.py` with baseline + exceptions ratchet |
| 5 — test split | complete | 930-line monolith split into 11 behaviour-scoped modules |
| 6 — lifecycle extraction | complete | `artifact_flow.py` 1036 → 330 lines; 10 capability modules |
| 7 — compatibility matrix | complete | `docs/compatibility/lifecycle-refactor-matrix.json` + import smoke |
| 8 — repo-wide audit | complete | `docs/milestones/gate-8-audit.md`; 0 failures, 97 warnings classified (including 4 restored legacy modules) |
| Fixtures | complete | 26/26 cases pass via `scripts/run_fixture_validation.py` |

## Required validation

| # | Validation | Result |
|---|---|---|
| 1 | size-checker unit tests | PASS — 25 tests in `scripts/tests/` (22 size-checker + 3 policy-discovery) |
| 2 | size-checker baseline-ratchet fixtures | PASS — unchanged / increased / reduced / stale / rename cases covered |
| 3 | focused lifecycle tests | PASS — 117 tests in `tests/audisor_lifecycle/` (92 artifact-flow + 25 legacy integration) |
| 4 | result-schema conformance tests | PASS — `test_artifact_result_schema.py` |
| 5 | compatibility-manifest comparison | PASS — `test_compatibility_manifest.py` |
| 6 | public import smoke tests | PASS — 40 parametrised cases in `test_public_import_smoke.py` |
| 7 | test discovery + exact collected count | PASS — 744 tests collected in the runtime suite |
| 8 | full repository test suite | PASS — 683 passed / 59 skipped / 2 deselected in runtime; 93 passed in `audisor_backend`; 56 passed in `aflow`; 25 passed in `scripts` |
| 9 | Python compilation | PASS — `compileall` clean over `audisor_lifecycle` |
| 10 | formatter check | unverified — `black` is not installed in this environment |
| 11 | lint check | unverified — `ruff` is not installed in this environment |
| 12 | type-check | unverified — `mypy` is not installed in this environment |
| 13 | schema validation | PASS — all 17 JSON schemas under `openai_project/schemas/` parse |
| 14 | package / build validation | PASS — `uv build` produced `audisor-0.10.0-py3-none-any.whl` + `.tar.gz`; runtime imports resolve |
| 15 | git diff whitespace check | PASS — no trailing whitespace in `openai_project/`, `scripts/`, `docs/` (pre-existing `web/` warnings are out of scope) |
| 16 | unexpected-file-change inspection | PASS — changes confined to `audisor_lifecycle/`, `tests/audisor_lifecycle/`, `scripts/`, `docs/`; no unrelated runtime / backend / web files touched by this task |

## Invariants preserved

- Result statuses unchanged: exactly `improved | unresolved_gap | skip | error`.
- Stage order unchanged: `gap_finding → gap_fixing → evaluation → success_criteria → fixture_design` with the unresolved-gap barrier between `gap_fixing` and `evaluation`.
- Bounded repair cycle: at most one re-fix per run; the digest-progress rule is enforced in code.
- Runtime-derived identity: `submission_digest`, `content_digest`, `artifact_revision`, `lifecycle_run_id`, `started_at`, `completed_at`, `evaluation_repair_cycles` — never agent-authored.
- Replay contract: identical submission digest against a persisted `improved` / `unresolved_gap` result returns `submission_disposition: replayed` without re-running stages.
- Line-ending-only canonicalisation: LF and CRLF digests match; trailing whitespace remains significant.
- Repair ceiling 8, REPAIR_POLICY `recorded-removal-v1`, TTL 900s — all unchanged.
- Persistence: atomic writes, errors/skips never replayed, `_persist_result` / `read_last_result` semantics preserved.
- Verbatim prompts and schemas: `STAGE_OUTPUT_SCHEMAS`, `_STAGE_INSTRUCTIONS`, `_STAGE_OUTPUT_EXAMPLES` — current prompt, schema, and output-example contracts are internally consistent and covered by the compatibility manifest and behavioural tests. Byte-identical continuity with the unretained pre-refactor source was not independently verified.
- Public MCP / CLI / import surface: every previously exported name remains importable from `audisor.audisor_lifecycle.artifact_flow` and from `audisor.audisor_lifecycle` (37-row compatibility matrix protected by 37 parametrised import cases plus two meta-tests and one count guard, for 40 tests total).

## Removal-compatibility matrix

The four legacy lifecycle integration modules were initially removed in
error and have been restored. They are separate lifecycle integration
surfaces, not part of the artifact_flow decomposition. The matrix below
proves every former caller is intact.

| Removed module (restored) | Why restoration belongs to this refactor | Replacement module(s) | Former public symbols | Every former caller | Compatibility path retained | Behavioural tests migrated |
|---|---|---|---|---|---|---|
| `active_state.py` | Not part of artifact_flow; separate active-state envelope writer for hook/plan_trigger/review_contract | N/A (restored as-is) | `default_state_root`, `write_active_state`, `read_active_state`, `clear_active_state`, `STATE_FILENAME` | `plan_trigger.py`, `review_contract.py`, `test_active_state.py` | Yes — module restored | Yes — `test_active_state.py` restored (25 tests pass) |
| `hook.py` | Not part of artifact_flow; Codex PreToolUse hook evaluator | N/A (restored as-is) | `evaluate_hook_payload`, `verify_active_state`, `HookInputError`, `HookVerificationError`, `HookAuditError`, `HookOutputError`, `MUTATING_TOOL_NAMES`, `MUTATING_COMMAND`, `READ_ONLY_TOOL_NAMES`, `PATCH_TARGET`, `PATH_IN_COMMAND` | `integrate.py` (line 308), `test_review_contract.py`, `.codex/hooks.json` (milestone-1 docs) | Yes — module restored; `integrate.py` caller intact | Yes — via `test_review_contract.py` |
| `plan_trigger.py` | Not part of artifact_flow; A-Flow plan review bridge | N/A (restored as-is) | `trigger_plan_review`, `_default_review_caller`, `_compute_digest`, `_is_valid_plan_for_review`, `AflowReviewCaller` | `active_state.py` (via `default_state_root`), `test_review_contract.py` | Yes — module restored | Yes — via `test_review_contract.py` |
| `review_contract.py` | Not part of artifact_flow; review→lock wrapper for A-Flow MCP path | N/A (restored as-is) | `map_decision_to_frozen`, `build_analysis_for_lock`, `assemble_review_result` | `plan_trigger.py`, `test_review_contract.py` | Yes — module restored | Yes — `test_review_contract.py` restored |

## Repository-wide caller search evidence

Repository-wide searches for every former module path confirm no
orphaned callers:

```
audisor_lifecycle.active_state    → test_active_state.py, test_review_contract.py, plan_trigger.py, review_contract.py (all restored)
audisor_lifecycle.hook            → integrate.py (line 308), test_review_contract.py, milestone-1 docs (all intact)
audisor_lifecycle.plan_trigger    → test_review_contract.py (restored)
audisor_lifecycle.review_contract → test_review_contract.py, plan_trigger.py (all restored)
```

No dynamic import strings, no plugin integrations, no user-scope
references, no backend callers, no A-Flow callers, no MCP server
callers beyond those listed. All callers are accounted for and intact.

## Validation integration

The size policy is authoritative because:

1. **Repository instruction**: `docs/code-size-policy.md` is the single
   authoritative size policy. It names `scripts/check_size.py` as the
   deterministic enforcement tool and `scripts/size_baseline.json` as
   the baseline ratchet file.
2. **Canonical validation command**: The size checker is executed via
   `scripts/run_fixture_validation.py` (fixture 26 — full repository
   validation), which runs `scripts/check_size.py` as part of the
   26-fixture suite. The runner exits non-zero when any fixture fails,
   including the size-checker step.
3. **Enforcement path**: `python scripts/check_size.py` → exit 0 = pass,
   exit 1 = policy failure, exit 2 = malformed configuration. The
   checker is deterministic, timestamp-free, and uses
   repository-relative POSIX paths.

The checker is integrated into the repository's canonical validation
command; external CI execution is not configured or was not verified.

## Test count traceability

The lifecycle test count increased from 91 to 117 during this task:

- **91 tests** after Gate 6 (artifact_flow decomposition + test split)
- **+1 test** for fixture 15 (no-progress rule): `test_repair_cycle_making_no_digest_progress_is_unresolved`
- **+25 tests** from restored legacy modules: `test_active_state.py` and `test_review_contract.py`
- **= 117 tests** in `tests/audisor_lifecycle/`

The full runtime suite increased from 617 to 683:

- **617 tests** before this task
- **+41 tests** from artifact-flow decomposition (40 import smoke + 1 no-progress fixture)
- **+25 tests** from restored legacy modules
- **= 683 tests** (683 passed / 59 skipped / 2 deselected)

The 744 collected tests include the 683 that ran plus 61 skipped/deselected by the marker filter.

## Unverified commands

The following commands required by the spec are not available in this
environment and are reported as unverified rather than claimed to have
passed:

- `black --check` (formatter)
- `ruff check` (linter)
- `mypy` (type-check)

None of these tools is installed in the runtime, backend, or aflow
virtual environments, and none is available on PATH. Their absence does
not affect the behavioural or compatibility evidence: 683 runtime tests
passed (59 skipped, 2 deselected), the 93-test backend suite, the
56-test aflow suite, and the 26-fixture runner all pass.

## Files produced by this task

New:

- `docs/code-size-policy.md` — authoritative size policy
- `docs/compatibility/lifecycle-refactor-matrix.json` — compatibility matrix
- `docs/milestones/gate-8-audit.md` — Gate 8 classification report
- `scripts/check_size.py` — deterministic size checker
- `scripts/size_baseline.json` — baseline ratchet file
- `scripts/size_exceptions.json` — exceptions configuration
- `scripts/run_fixture_validation.py` — 26-fixture runner
- `scripts/tests/test_check_size.py` — size-checker behavioural tests
- `scripts/tests/test_fixture_policy_discovery.py` — fixture 1
- `openai_project/runtime/src/audisor/audisor_lifecycle/active_runs.py`
- `openai_project/runtime/src/audisor/audisor_lifecycle/artifact_flow.py` (rewritten as orchestrator)
- `openai_project/runtime/src/audisor/audisor_lifecycle/evaluation_repair.py`
- `openai_project/runtime/src/audisor/audisor_lifecycle/identity.py`
- `openai_project/runtime/src/audisor/audisor_lifecycle/output_processing.py`
- `openai_project/runtime/src/audisor/audisor_lifecycle/persistence.py`
- `openai_project/runtime/src/audisor/audisor_lifecycle/result_builder.py`
- `openai_project/runtime/src/audisor/audisor_lifecycle/stage_contracts.py`
- `openai_project/runtime/src/audisor/audisor_lifecycle/stage_execution.py`
- `openai_project/runtime/src/audisor/audisor_lifecycle/stage_prompts.py`
- `openai_project/runtime/src/audisor/audisor_lifecycle/stage_worker.py`
- `openai_project/runtime/tests/audisor_lifecycle/__init__.py`
- `openai_project/runtime/tests/audisor_lifecycle/flow_fixtures.py`
- `openai_project/runtime/tests/audisor_lifecycle/manifest_builder.py`
- `openai_project/runtime/tests/audisor_lifecycle/test_artifact_active_runs.py`
- `openai_project/runtime/tests/audisor_lifecycle/test_artifact_evaluation_repair.py`
- `openai_project/runtime/tests/audisor_lifecycle/test_artifact_gap_synthesis.py`
- `openai_project/runtime/tests/audisor_lifecycle/test_artifact_identity_and_replay.py`
- `openai_project/runtime/tests/audisor_lifecycle/test_artifact_output_repair.py`
- `openai_project/runtime/tests/audisor_lifecycle/test_artifact_persistence.py`
- `openai_project/runtime/tests/audisor_lifecycle/test_artifact_result_schema.py`
- `openai_project/runtime/tests/audisor_lifecycle/test_artifact_stage_execution.py`
- `openai_project/runtime/tests/audisor_lifecycle/test_artifact_trigger_and_barrier.py`
- `openai_project/runtime/tests/audisor_lifecycle/test_compatibility_manifest.py`
- `openai_project/runtime/tests/audisor_lifecycle/test_public_import_smoke.py`

Modified:

- `openai_project/runtime/src/audisor/audisor_lifecycle/__init__.py` — re-exports adjusted to the new layout

**No existing runtime modules were removed.** The four legacy lifecycle
integration modules (`active_state.py`, `hook.py`, `plan_trigger.py`,
`review_contract.py`) and their two test modules were initially removed
in error during the refactor and have been restored. They are separate
lifecycle integration surfaces (Codex hook evaluator, A-Flow plan
bridge, review→lock wrapper, active-state envelope writer) — not part
of the artifact_flow decomposition.
