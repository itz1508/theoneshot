# A-Flow Runtime Reliability Repairs A-C

Status: `already_applied`
Repository: `D:\Dev\Theoneshot`
Planning baseline: branch `preserve/shadcn-sidebar-baseline`, HEAD `2f04d8a1c8d092e607394f967cad5b934ce9fe9d`
Gap records: `gaps/AFR-01-readiness.md`, `gaps/AFR-02-timeout-isolation.md`, `gaps/AFR-03-stage-schema.md`
Post-build fixtures: `fixture-specifications.md`

## Objective

Repair the three confirmed A-Flow defects that have established ownership:

1. configuration-bound, expiring, truthfully reported provider readiness;
2. provider HTTP execution that is terminated and reaped on timeout;
3. delivery of the real stage schema with strict, evidence-preserving validation.

Automatic-caller wiring remains deferred until one production owner is established. Global `aflow` and `audisor-local` installation changes require a separate external approval.

## Root Cause

### AFR-01 - readiness

- Observed symptom: historical readiness remains `valid` and `can_submit=true` after provider availability may have changed.
- Root cause: probe currency is determined only by provider/model/endpoint fingerprint equality; probe age, schema mode, adapter contract version, and a live submission check are absent. `can_submit` means configured rather than currently authorized.
- Why this plan targets the cause: it separates cached qualification from same-call submission authorization and binds both to the complete execution configuration.

### AFR-02 - timeout isolation

- Observed symptom: the caller returns a timeout while `aflow-<stage>` continues until the provider call eventually returns.
- Root cause: `ThreadPoolExecutor.shutdown(wait=False)` cannot terminate an active Python thread or its blocking `requests` call.
- Why this plan targets the cause: default production HTTP calls move into owned child processes that can be terminated and reaped before retry or terminal persistence.

### AFR-03 - stage schema

- Observed symptom: provider output is valid JSON but lacks required `gaps`; the exact validator failure is later reduced to a generic parse error.
- Root cause: the adapter requests a generic JSON object instead of the stage schema, while the worker discards the validation path and message.
- Why this plan targets the cause: the schema reaches every provider mode and strict validation retains bounded, non-secret evidence without synthesizing required fields.

## Approved Boundary and Preflight

- Before mutation, capture the current branch, HEAD, and literal `git status --porcelain=v1 --untracked-files=all` in external command evidence.
- Recheck every target path below. All existing targets were clean during planning; any later target diff stops mutation.
- Do not create or manage a new rollback snapshot. Do not modify the temporary Hermes rollback snapshot.
- For tracked targets, record pre-mutation SHA-256 values and use the exact scoped Git patch as the rollback boundary. Back up only a pre-existing unversioned file that must be edited. Newly created implementation files are removed on rollback; append-only Gap Records remain and receive a `failed` entry.
- Do not stage, commit, push, publish, deploy, clean, or change external tool installations.

Expected existing targets:

- `openai_project/runtime/src/audisor/audisor_lifecycle/{management,artifact_flow,stage_execution,stage_worker}.py`
- `openai_project/runtime/src/audisor/workers/{base,local,fireworks}.py`
- `openai_project/runtime/src/audisor/routing/configuration.py` and `src/audisor/config/__init__.py` only if the frozen provider configuration contract requires them;
- focused tests under `openai_project/runtime/tests/`

Expected additive targets:

- internal isolated-HTTP parent/child modules under `audisor.workers`;
- `tests/test_isolated_http.py`;
- `tests/test_aflow_repair_fixtures.py` and deterministic JSON/text cases under `tests/fixtures/aflow_repairs/`;
- `tests/aflow_mcp/test_live_local_lifecycle.py`;
- the three Gap Records and fixture specification in this directory.

Neighboring lifecycle files already contain unrelated dirty work. They remain excluded; clean status is required for the exact existing targets named above, not their containing directories.

## Repair A - Configuration-Bound Readiness

1. Add a five-minute full structured-probe lifetime and a five-second live submission check using an injectable clock. Reject impossible future timestamps as `clock_invalid`.
2. Canonically hash these fingerprint fields:
   - provider ID;
   - provider protocol;
   - normalized endpoint;
   - model ID;
   - proven schema mode (`native_json_schema` or `prompt_validated_json`);
   - stable adapter identity and adapter contract version;
   - a secret-free digest of every behavior-affecting provider setting;
   - readiness contract revision `2`;
   - canonical stage-schema-set revision.
3. Persist `checked_at`, `expires_at`, fingerprint inputs, outcome, schema mode, and monotonic readiness generation.
4. Serialize probe/invalidation read-modify-write operations with a cross-process lock adjacent to `provider-probes.json`. Write through a unique temporary file, flush/fsync it, atomically replace, and fsync the directory where supported.
5. Report cached readiness as `current`, `expired`, `configuration_mismatch`, `missing`, `invalidated`, or `live_check_failed`.
6. `provider_status(probe=False)` performs no network I/O and must not represent cached qualification as live authorization. Its public result includes `live_check_required=true`; `can_submit=false` until same-call admission succeeds.
7. Provider adapters, not lifecycle management, own `run_full_readiness_probe(frozen_configuration)` and `run_live_submission_check(frozen_configuration)`. They select provider-specific URLs and protocols.
8. At the start of every lifecycle, before submission snapshot or running-marker persistence, freeze the provider configuration and fingerprint. A missing, expired, clock-invalid, or mismatched record triggers one bounded full adapter probe; a fresh record triggers one bounded adapter live check. A successful full probe counts as the live check for that submission.
9. The successful live check authorizes only that lifecycle call; it is not reusable authorization for another submission.
10. Invalidate endpoint/model readiness after transport, authentication, missing-model, configuration, adapter, or readiness-revision failures. Invalidate native-schema readiness when native enforcement is rejected or violated. A single malformed `prompt_validated_json` answer is `provider_contract_invalid` but does not by itself mark the endpoint or model unavailable. A concurrent older probe cannot overwrite a newer invalidation.
11. Injected deterministic workers use deterministic implementations of the same readiness interface; they do not bypass admission semantics.

## Repair B - Terminable HTTP Execution

1. Add an internal isolated-HTTP transport. The parent starts the current runtime interpreter with a fixed module entrypoint, never a shell.
2. Send method, URL, headers, JSON body, timeout, and response-size limit through stdin. Never place credentials in arguments, environment variables, persisted state, stdout diagnostics, or logs.
3. The child performs exactly one request and emits one JSON envelope containing status, bounded response data, and normalized transport metadata. It performs no retry, fallback, parsing authority, lifecycle mutation, or persistence.
4. Enforce independent bounds: response body 1,048,576 bytes; stdout protocol 1,114,112 bytes; stderr diagnostics 65,536 bytes. Read stdout and stderr concurrently into bounded buffers while the child runs. Kill and reap immediately at any limit; never call an unbounded `communicate()` and check afterward.
5. Give the HTTP child the provider attempt budget minus a two-second termination reserve. If that leaves no positive budget, do not start it.
6. On timeout, terminate the process, wait one second, kill if still alive, then wait one more second. Persist the terminal attempt only after confirmed reap. If exit cannot be confirmed, classify `provider_process_reap_failed` and stop without retry or fallback.
7. Create a new process group on Windows and a new session on POSIX. The child must not spawn descendants.
8. Route default Local and Fireworks request functions through the isolated transport. Preserve injected request functions for unit tests only.
9. Extend provider capabilities with `terminable_execution`, defaulting to `false` for compatibility with existing adapters. Local and Fireworks report `true` only when using the isolated default transport. Production `ManagedStageWorker` refuses a provider without this capability; test-only injected workers remain supported only through the explicit injected-worker lifecycle path.
10. Retry or fallback begins only after the prior child is absent. Timed-out stdout is discarded, and the child has no operation-store or lifecycle-state authority. Persist `client_process_terminated=true` separately from `server_inference_cancelled=unverified`.
11. Keep the outer stage deadline as a defensive bound. Production provider budget plus termination reserve must finish before that bound, preventing a surviving orchestration thread on normal provider timeout.
12. Normalize at least: `provider_transport_failed`, `provider_authentication_failed`, `provider_model_unavailable`, `provider_response_too_large`, `provider_timeout`, `provider_process_reap_failed`, `stage_budget_exhausted`, and `provider_protocol_invalid`.

## Repair C - Real Stage-Schema Contract

1. Define a typed schema-aware provider method accepting the task, canonical stage schema, schema name, and proven schema mode.
2. Include the complete canonical Draft 2020-12 stage schema in every stage prompt.
3. For `native_json_schema`, the adapter first compiles the canonical schema without dropping required properties, nested objects, arrays, enums, additional-properties behavior, references/definitions, or type constraints. Local OpenAI-compatible requests then send the strongest compatible strict native constraint. A capability probe must prove the endpoint accepts it and returns the exact probe object before this mode is recorded.
4. If the endpoint explicitly rejects native constraints, retry the qualification probe once using `prompt_validated_json`. This mode may use generic JSON-object response formatting, but it is never described as native enforcement.
5. Fireworks remains `prompt_validated_json` unless its configured endpoint independently proves the native contract; do not infer support from provider name or model ID.
6. Validate every response against the existing stage validator regardless of provider mode. Do not synthesize `gaps`, weaken `required`, or turn malformed output into no-material-gap.
7. Distinguish `provider_response_not_json`, `provider_schema_unsupported`, `provider_schema_request_rejected`, and `provider_contract_invalid`; include them in management issue classification and recovery guidance.
8. Preserve bounded evidence: stage, JSON path, validator keyword, exact validator message, provider, model, attempt ID, schema mode/digest, response digest, and response byte count. Do not persist the raw provider answer in public issue records.
9. Keep recorded removal limited to the existing allowed unknown-field policy. Missing required fields are never repairable.

## Ordered Implementation

1. Append `in_progress` to each Gap Record and recapture target status/hashes.
2. Create the deterministic fixture data and initially failing focused tests.
3. Implement B0, the isolated HTTP foundation, and validate termination/reaping independently.
4. Implement Repair A on top of adapter-owned isolated readiness probes.
5. Implement B1 by migrating Local and Fireworks production HTTP calls without changing their unrelated request semantics.
6. Implement Repair C and run native/prompt schema and diagnostic fixtures.
7. Run the combined MCP/lifecycle tests and complete runtime suite.
8. Inspect the exact diff, scoped whitespace, temporary files, child processes, `aflow-*` threads, and pre/post status by path.
9. Only after deterministic validation exits `0`, run the explicitly enabled real local-provider fixture. Do not increase the 120-second provider budget during this build.
10. Append `validated` to each Gap Record only after its independent fixtures and the integrated phase review complete.

## Phase Acceptance — 2026-07-31T05:09:57Z

- Deterministic fixture inventory: every declared non-live fixture has a collected executable node; missing count `0`.
- Focused A-C integration: `129 passed, 2 skipped`, exit code `0`.
- Independent post-build fixtures: `35 passed`, exit code `0`.
- Complete runtime regression: `774 passed, 61 skipped`, exit code `0` in 42.64 seconds.
- Scoped `git diff --check`: exit code `0`; no temporary files or operation-caused path outside the approved phase boundary.
- Live provider fixture: `not_run`, retained as a separately authorized external validation boundary.

## Validation Commands

```powershell
# Repair A
openai_project\runtime\.venv\Scripts\python.exe -m pytest `
  openai_project\runtime\tests\audisor_lifecycle\test_aflow_management.py `
  openai_project\runtime\tests\aflow_mcp\test_transport.py -q

# Repair B
openai_project\runtime\.venv\Scripts\python.exe -m pytest `
  openai_project\runtime\tests\test_isolated_http.py `
  openai_project\runtime\tests\test_worker_adapter.py `
  openai_project\runtime\tests\audisor_lifecycle\test_artifact_stage_execution.py -q

# Repair C
openai_project\runtime\.venv\Scripts\python.exe -m pytest `
  openai_project\runtime\tests\test_worker_adapter.py `
  openai_project\runtime\tests\audisor_lifecycle\test_artifact_stage_execution.py `
  openai_project\runtime\tests\audisor_lifecycle\test_aflow_management.py -q

# Combined deterministic validation
openai_project\runtime\.venv\Scripts\python.exe -m pytest `
  openai_project\runtime\tests\aflow_mcp `
  openai_project\runtime\tests\audisor_lifecycle `
  openai_project\runtime\tests\test_worker_adapter.py `
  openai_project\runtime\tests\test_isolated_http.py -q

# Runtime regression
openai_project\runtime\.venv\Scripts\python.exe -m pytest

# Live fixture, only after all deterministic commands exit 0
$env:AUDISOR_RUN_LIVE_AFLOW = "1"
openai_project\runtime\.venv\Scripts\python.exe -m pytest `
  openai_project\runtime\tests\aflow_mcp\test_live_local_lifecycle.py -q -s
Remove-Item Env:AUDISOR_RUN_LIVE_AFLOW
```

Run `git diff --check` with the exact changed A-C paths, then compare the literal final status against preflight. Every command must record exit code and wall time.

## Plan Evaluation

Diagnostic evidence: `valid`
Proposal state: `review_required`

### Gap-review findings and corrections

| Affected draft step | Gap found | Why it mattered | Correction in this plan | Blocks execution after correction |
|---|---|---|---|---|
| Repair A authorization | Cached qualification was still capable of being described as live `can_submit` authority. | A stopped provider could appear authorized without a same-call check. | Static status now reports `live_check_required`; every production submission performs a non-reusable live model check before persistence. | no |
| Repair A persistence | Probe refresh and failure invalidation had no ordering contract. | An older successful probe could overwrite a newer failure. | Add cross-process serialization, readiness generations, atomic durable writes, and generation-aware invalidation. | no |
| Repair B provider scope | HTTP-only isolation did not prove that every production provider used the isolated transport. | A custom provider could retain the original surviving-thread behavior. | Add `terminable_execution=false` by default and reject non-terminable production providers. | no |
| Repair B output bound | A size limit without streaming semantics could still buffer unbounded child output. | The isolation layer itself could exhaust memory. | Read both streams into bounded buffers and kill/reap immediately at the combined 1 MiB limit. | no |
| Repair B acceptance | The fixtures checked child PIDs but not the outer `aflow-*` orchestration thread. | A reaped child alone would not prove the caller lifecycle was clean. | Require zero matching child processes and zero `aflow-*` threads when timeout returns. | no |
| Repair C capability behavior | Native schema support was assumed too broadly and fallback evidence was vague. | Provider identity or generic JSON support does not prove full-schema enforcement. | Native mode requires a successful capability probe; unsupported endpoints use full-schema prompt mode with unchanged strict validation. | no |
| Validation boundary | The live fixture command referenced a file absent from the additive target list. | The implementer could omit the acceptance fixture while still claiming coverage. | Add the live MCP fixture to the explicit target boundary and ordered validation. | no |

### Requirement evaluation

| Requirement | Evaluation |
|---|---|
| Exact readiness fingerprint and expiry | `valid` - fields, revisions, five-minute lifetime, and invalidation ordering are explicit. |
| Chosen timeout mechanism and rollback | `valid` - HTTP child termination/reap is specified; rollback uses only scoped Git patch and per-file backup rules. |
| Unsupported full-schema behavior | `valid` - prompt-validated mode receives the complete schema and remains strictly validated. |
| Per-step validators and success criteria | `valid` - focused, combined, regression, and live commands plus fixture observations are specified. |
| Clean-path mutation gate | `valid` - exact targets were clean during planning and must be rechecked immediately before mutation. |
| Automatic caller | `uncertainty` but out of scope - Repair D remains independently blocked on ownership. |
| External installations | `valid` exclusion - Repair E requires separate approval and cannot be changed by A-C. |

The revised plan closes the draft's material gaps:

- cached readiness is no longer presented as same-call authorization;
- readiness is bound to schema mode and adapter revision, not only endpoint/model;
- invalidation cannot be overwritten by an older concurrent probe;
- HTTP-only isolation is enforced as a production provider capability;
- output limits are enforced during streaming rather than after unbounded buffering;
- retry ordering includes confirmed process reap;
- unsupported native schema behavior is explicit and still strictly validated;
- exact schema-path evidence and normalized classification are specified;
- rollback is scoped without creating or reusing a snapshot;
- deterministic and live fixtures have explicit order, commands, and success criteria.

The plan-gap review found no remaining execution blocker inside Repairs A-C. Implementation still requires path-specific mutation approval and a fresh clean-target preflight.

Repair D remains blocked only on production-owner identification. Repair E remains excluded pending external mutation approval. Neither blocks A-C.

## Successfully Built State

The build is successful only when all of the following are directly evidenced:

```yaml
provider_readiness:
  cached_probe: current
  configuration_match: true
  schema_mode_proven: true
  live_check_for_submission: valid
  stale_authorization_reused: false

timeout_control:
  timed_out_child_reaped: true
  remaining_provider_children: 0
  remaining_aflow_threads: 0
  late_state_mutation: false
  overlapping_retry: false
  terminal_attempt_records: 1

stage_contract:
  canonical_schema_delivered: true
  strict_post_validation: true
  missing_required_fields_synthesized: false
  validation_path_preserved: true

scope:
  unrelated_dirty_paths_changed: false
  repair_d_changed: false
  external_installations_changed: false
```

The real local-provider fixture must either complete with a schema-valid lifecycle result or return a precise provider-capacity classification while leaving zero provider children. Only then may the remaining model-capacity question be evaluated independently.
