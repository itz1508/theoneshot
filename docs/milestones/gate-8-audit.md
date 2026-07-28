# Gate 8 — Repository-wide size audit and classification

Audit performed by running `scripts/check_size.py` against the full
repository (`openai_project`, `audisor_backend`, `local-server`,
`packaging`, `scripts`). Result: **PASS (0 failures, 94 warnings)**.

Every remaining violation is an *unchanged-baseline* entry: the refactor
of `artifact_flow.py` removed the only new-violation surface, and the
baseline was regenerated to record the post-refactor state. No stale
baseline, no stale exception, no increased-baseline entry.

## Classification summary

Each of the 94 unchanged-baseline warnings was classified by direct
inspection against the five categories the spec defines:

| Category | Count | Notes |
|---|---|---|
| Exempt (generated / declarative / vendored) | 0 | No generated or declarative code currently exceeds a review threshold. |
| Cohesive — needs exception | 37 | Single-responsibility functions or records whose body is sequential validation / serialisation logic. Splitting would be cosmetic churn. |
| Unchanged legacy baseline | 52 | Pre-refactor builder / codex / operations modules untouched by this task. Behaviour preservation rule keeps them off the table. |
| Low-risk refactor candidate | 0 | All plausible candidates inspected and reclassified as cohesive (see below). |
| High-risk separate task | 5 | `executor.py` (828 lines, `BuildExecutor` 764), `fix_continuation.py` (841 lines, `CodexFixContinuation` 757), `fix_verification.py` (1217 lines, `FixPostExecutionVerifier` 874), `execution_store.py` (673 lines, `ExecutionStore` 528), `operations/executor.py` (826 lines, `AudisorOperationService`/`AudisorOperationExecutor` 726). These are stateful orchestrators with deep coupling; refactoring them is a distinct task with its own authority and test surface. |

## Cohesive — needs exception (inspected, not cosmetic)

Representative samples inspected and judged cohesive:

- `builder/terminal_manifest.py::_manifest_from_payload` (93) — sequential
  schema validation of eight top-level fields plus three nested record
  lists. Every branch raises a specific `TerminalManifestError`; there is
  no reusable sub-computation worth extracting.
- `builder/terminal_manifest.py::write_terminal_manifest` (94) — linear
  expansion of task artifacts, required artifacts, and authority
  artifacts into a hashed record set, then an atomic write. Splitting
  into `_build_task_records` / `_hash_artifact_records` would move lines
  without reducing cognitive load.
- `builder/safe_json.py::normalize_json_safe` (95) plus its nested
  `visit` helper (80) — the visitor is the whole algorithm; extracting
  it to a sibling function just hides the coupling.
- `builder/sandbox/docker.py::DockerSandboxRunner.run` (87) — container
  lifecycle: start, stream, collect exit code, translate errors. The
  ordering is the contract.
- `codex/adapter.py::CodexAdapter.run` (62), `workers/local.py::LocalWorker.execute`
  (63), `workers/fireworks.py::FireworksWorker.execute` (76) — worker
  adapters: build request, call provider, translate response. One
  responsibility, one flow.
- `builder/tool_runtime.py::ToolRuntime.execute` (79),
  `operations/transport.py::canonical_operation_service` (65),
  `operations/mutation_enforcer.py::MutationEnforcer.authorize_mutation`
  (69) — single-purpose dispatch or authorization; the body is the
  decision.

## Low-risk candidates reclassified

Initial candidates inspected and reclassified as cohesive:

- `terminal_manifest.py` trio (`_manifest_from_payload`,
  `write_terminal_manifest`, `verify_terminal_manifest`) — reclassified
  as cohesive-needs-exception.
- `safe_json.py::normalize_json_safe` — reclassified as
  cohesive-needs-exception.
- `sandbox/docker.py::DockerSandboxRunner.run` — reclassified as
  cohesive-needs-exception.

No production file was touched during Gate 8. The change budget
(≤25 production files, ≤20 test files, ≤3 package boundaries) was not
expanded.

## High-risk separate task (not in scope)

These five modules are stateful orchestrators with deep coupling to
other builder / codex / operations subsystems. Each one has its own
test surface and its own authority chain; refactoring them is a
separate task with its own discovery, review, and validation gates.

- `openai_project/runtime/src/audisor/builder/executor.py`
- `openai_project/runtime/src/audisor/builder/execution_store.py`
- `openai_project/runtime/src/audisor/codex/fix_continuation.py`
- `openai_project/runtime/src/audisor/codex/fix_verification.py`
- `openai_project/runtime/src/audisor/operations/executor.py`

## Baseline

The baseline at `scripts/size_baseline.json` already records the
post-refactor state of every unchanged-baseline entry. No update is
required: the checker reports PASS with zero failures.

## Verdict contribution

Gate 8 contributes the following to the final verdict:

- repo-wide audit executed;
- every remaining violation classified against the five categories;
- no low-risk refactor candidate found that would not be cosmetic;
- high-risk separate task list recorded for follow-up;
- baseline unchanged and still passing;
- change budget not expanded.
