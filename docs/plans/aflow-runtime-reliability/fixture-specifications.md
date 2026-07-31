# Post-Build Fixture Specifications

These fixtures run after their corresponding implementation repair. They do not authorize production mutation by themselves.

## AFR-01 - readiness fixtures

| Node ID | Setup | Required observation |
|---|---|---|
| `test_expired_probe_requires_requalification` | Persist a matching probe older than 300 seconds. | Status is `expired`; no lifecycle snapshot, marker, or provider generation occurs. |
| `test_expired_probe_runs_one_full_probe` | Persist an expired matching record and supply a successful deterministic adapter probe. | Exactly one full probe runs, its configuration is frozen, and admission uses that same fingerprint. |
| `test_every_fingerprint_field_invalidates_probe` | Parameterize provider, endpoint, model, schema mode, adapter version, and contract revision changes. | Each change yields `configuration_mismatch` and blocks authorization. |
| `test_future_probe_timestamp_is_clock_invalid` | Persist a readiness timestamp beyond the allowed clock tolerance. | State is `clock_invalid`; cached success is not used. |
| `test_stopped_provider_cannot_use_cached_probe` | Persist a fresh matching probe; make `/models` unreachable. | Same-call authorization returns `live_check_failed`, invalidates the generation, and starts no lifecycle. |
| `test_configured_model_must_be_live` | `/models` returns 200 without the configured model. | Classification is `model_unavailable`; `can_submit` remains false. |
| `test_older_probe_cannot_overwrite_newer_invalidation` | Interleave a probe write with a later failure invalidation. | Final generation is invalidated and the earlier writer cannot restore `current`. |

## AFR-02 - timeout fixtures

| Node ID | Setup | Required observation |
|---|---|---|
| `test_hanging_http_child_is_terminated_and_reaped` | Child accepts a request and never returns. | Timeout is bounded; the recorded PID no longer exists before return. |
| `test_timeout_leaves_no_aflow_thread` | Run a production `ManagedStageWorker` against the hanging isolated endpoint. | No thread whose name begins `aflow-` remains when the timeout result is returned. |
| `test_timeout_discards_late_output` | Child attempts to emit a valid response after the deadline. | No response is consumed and lifecycle/operation state bytes remain unchanged. |
| `test_retry_waits_for_previous_child_reap` | First attempt hangs; second is immediately successful. | Second start timestamp is later than first reap timestamp; maximum concurrent children is one. |
| `test_timeout_persists_one_terminal_attempt` | Force terminate/kill path. | Exactly one timeout attempt is recorded and repeated cleanup is idempotent. |
| `test_unreaped_child_stops_retry` | Make terminate and kill report no confirmed exit. | Result is `provider_process_reap_failed`; retry and fallback call counts remain zero. |
| `test_production_rejects_nonterminable_provider` | Resolve a production provider reporting `terminable_execution=false`. | No provider call begins; result is a capability/configuration error. |
| `test_child_request_never_exposes_credentials` | Use a sentinel API key and inspect command line, environment, persisted evidence, stdout, and stderr. | Sentinel appears only in child stdin/request headers and nowhere observable afterward. |
| `test_oversized_child_output_is_killed_while_streaming` | Child streams more than 1 MiB without exiting. | Parent kills and reaps at the limit without retaining the complete output in memory. |

## AFR-03 - schema fixtures

| Node ID | Setup | Required observation |
|---|---|---|
| `test_native_mode_sends_exact_stage_schema` | Native-capable fake endpoint captures the request. | Captured schema canonical bytes equal `STAGE_OUTPUT_SCHEMAS[stage]`; strict mode is enabled. |
| `test_unsupported_native_mode_uses_prompt_validated_json` | Native request returns an explicit unsupported-feature response; prompt JSON succeeds. | One controlled fallback occurs; recorded mode is `prompt_validated_json`. |
| `test_prompt_mode_contains_complete_schema` | Provider lacks native support. | Prompt contains the complete canonical schema and output still undergoes strict validation. |
| `test_missing_gaps_preserves_validator_evidence` | Return `{}` for `gap_finding`. | Code is `provider_contract_invalid`; path is `$`; validator is `required`; message identifies `gaps`; no field is synthesized. |
| `test_invalid_contract_invalidates_readiness` | Begin with current readiness, then return schema-invalid output. | Matching readiness generation becomes invalidated before retry authority is offered. |
| `test_prompt_contract_failure_keeps_endpoint_readiness` | Prompt-validated provider returns one malformed response. | Attempt is `provider_contract_invalid`, but endpoint/model readiness is not invalidated solely by that response. |
| `test_valid_constrained_response_advances_once` | Return `{"gaps": []}` under the proven mode. | Gap finding completes exactly once without repair or duplicate transition. |

## Combined and live fixtures

| Node ID | Setup | Required observation |
|---|---|---|
| `test_live_local_lifecycle` | Explicit `AUDISOR_RUN_LIVE_AFLOW=1`, current Ollama model, deterministic small artifact. | Canonical MCP lifecycle returns schema-valid completion or precise capacity classification; zero provider children remain. |
| `test_no_unrelated_path_changes` | Compare preflight and final status plus scoped diff. | Only approved A-C implementation, tests, plan, and Gap Record paths differ. |

Fixture success requires exact node ID, command, exit code, wall time, relevant process evidence, and resulting artifact/issue identity. A timeout is evidence of the classified outcome, never implicit approval or completion.
