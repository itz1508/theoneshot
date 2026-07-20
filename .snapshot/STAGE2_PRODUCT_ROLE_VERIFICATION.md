# Stage 2 Product-Role Verification — A-Flow vs Audisor Identity

## Result: `correction_required` → `rename_verified`

A product-role misattribution was found and corrected during verification. The rename is now verified.

---

## Correction Applied

| Location | Misattribution | Correction | Product Owner |
|---|---|---|---|
| `cli.py` line 38 | `Audisor review: ON` | `A-Flow: ON` | A-Flow |
| `cli.py` line 148 | `Audisor review: ON/OFF` | `A-Flow: ON/OFF` | A-Flow |
| `plan_trigger.py` line 1 | `Automatic Audisor plan review` | `Automatic A-Flow plan review` | A-Flow |
| `plan_trigger.py` line 41 | `Audisor review must have` | `A-Flow review must have` | A-Flow |
| `plan_trigger.py` line 119 | `plan_document does not meet Audisor trigger` | `plan_document does not meet A-Flow trigger` | A-Flow |
| `plan_trigger.py` line 156 | `Audisor requires human decision` | `A-Flow requires human decision` | A-Flow |
| `plan_trigger.py` line 166 | `unexpected Audisor outcome` | `unexpected A-Flow outcome` | A-Flow |
| `plan_trigger.py` line 286 | `Audisor review passed` | `A-Flow review passed` | A-Flow |
| `test_cli.py` line 17,20,29 | `Audisor review: ON/OFF` | `A-Flow: ON/OFF` | A-Flow |
| `test_audisor_auto_trigger.py` line 1 | `automatic Audisor plan review` | `automatic A-Flow plan review` | A-Flow |
| `test_audisor_auto_trigger.py` line 27 | `triggers Audisor review` | `triggers A-Flow review` | A-Flow |

---

## Symbol-by-Symbol Verification Trace

### 1. `audisor_lifecycle` (package)

```yaml
verification:
  symbol: audisor_lifecycle
  current_name: audisor_lifecycle
  actual_behavior: |
    Mixed. Contains:
    - Local Audisor execution: contract.py (lock/verify), adapter.py (contract assembly),
      ignition.py (local worker invoke, build analysis), local_invoker.py (local model call),
      artifacts.py (stage persistence), analysis_package.py (package assembly)
    - A-Flow bridge: plan_trigger.py (auto_trigger_plan_review calls aflow_review)
  callers:
    - audisor_run_gate.py → operation, ignition
    - builder/executor.py → operation
    - codex/adapter.py → contract, ignition, adapter
    - cli.py → audisor_run_gate (indirect)
  product_owner: shared_adapter
  rename_correct: true
  required_action: preserve
  evidence:
    - file: openai_project/runtime/src/audisor/audisor_lifecycle/__init__.py
      symbol: __all__ exports both Audisor and A-Flow bridge symbols
    - file: openai_project/runtime/src/audisor/audisor_lifecycle/plan_trigger.py
      symbol: auto_trigger_plan_review
```

**Verdict:** The package is a **shared adapter** — it hosts both the local Audisor execution core AND the A-Flow automatic review bridge. The rename `aflow_lifecycle → audisor_lifecycle` is correct because the package's primary responsibility is local Audisor lifecycle management; the A-Flow bridge (`plan_trigger.py`) is a secondary integration module. The package name should reflect the primary owner.

---

### 2. `AudisorIndicator` (class)

```yaml
verification:
  symbol: AudisorIndicator
  current_name: AudisorIndicator
  actual_behavior: |
    Terminal spinner shown during local Audisor preflight (local model worker call).
    Used in ignition.py during evaluate_build() and local_invoker paths.
    NOT used in A-Flow automatic review (plan_trigger.py has no UI).
  callers:
    - ignition.py line 91: with AudisorIndicator()
  product_owner: audisor
  rename_correct: true
  required_action: preserve
  evidence:
    - file: openai_project/runtime/src/audisor/audisor_lifecycle/indicator.py
      symbol: AudisorIndicator.__init__
    - file: openai_project/runtime/src/audisor/audisor_lifecycle/ignition.py
      symbol: ignite() local worker path
```

**Verdict:** Correctly renamed. This is a local Audisor UI component, not A-Flow.

---

### 3. `AudisorGateResult` (class)

```yaml
verification:
  symbol: AudisorGateResult
  current_name: AudisorGateResult
  actual_behavior: |
    Result of the preflight gate for `audisor run` CLI command.
    Checks frozen policy (which wraps is_aflow_enabled()) and calls ignite().
    When A-Flow is OFF, gate permits immediately. When ON, runs Audisor lifecycle.
    The gate is Audisor's entrypoint, not A-Flow's.
  callers:
    - audisor_run_gate.py: check_aflow_gate() returns AudisorGateResult
    - cli.py: run command checks gate.permitted
  product_owner: audisor
  rename_correct: true
  required_action: preserve
  evidence:
    - file: openai_project/runtime/src/audisor/audisor_run_gate.py
      symbol: AudisorGateResult
    - file: openai_project/runtime/src/audisor/cli.py
      symbol: check_aflow_gate(batch.root)
```

**Verdict:** Correctly renamed. This is the Audisor run gate, not A-Flow. The gate checks A-Flow policy but is Audisor's boundary.

---

### 4. `AudisorLifecycleError` (class)

```yaml
verification:
  symbol: AudisorLifecycleError
  current_name: AudisorLifecycleError
  actual_behavior: |
    Base exception for all lifecycle transitions. Raised by:
    - contract.py (lock malformed, readiness unknown)
    - ignition.py (incomplete context, malformed plan)
    - local_invoker.py (provider failure, schema failure)
    - plan_trigger.py (review caller not configured)
    - adapter.py (contract assembly failure)
  callers:
    - contract.py: raise AudisorLifecycleError
    - ignition.py: raise AudisorLifecycleError
    - local_invoker.py: raise AudisorInvocationError (subclass)
    - plan_trigger.py: raise AudisorLifecycleError
  product_owner: audisor
  rename_correct: true
  required_action: preserve
  evidence:
    - file: openai_project/runtime/src/audisor/audisor_lifecycle/contract.py
      symbol: class AudisorLifecycleError
    - file: openai_project/runtime/src/audisor/audisor_lifecycle/ignition.py
      symbol: select_candidate_plan raises
```

**Verdict:** Correctly renamed. This is the local Audisor lifecycle exception. Used by A-Flow bridge only because it shares the same lifecycle package.

---

### 5. `AudisorInvocationError` (class)

```yaml
verification:
  symbol: AudisorInvocationError
  current_name: AudisorInvocationError
  actual_behavior: |
    Structured fail-closed error from the local Audisor model bridge.
    Raised when local worker fails, schema validation fails, hash mismatch, etc.
    Subclass of AudisorLifecycleError.
  callers:
    - local_invoker.py: raise AudisorInvocationError
    - ignition.py: catches and re-raises
  product_owner: audisor
  rename_correct: true
  required_action: preserve
  evidence:
    - file: openai_project/runtime/src/audisor/audisor_lifecycle/local_invoker.py
      symbol: class AudisorInvocationError
```

**Verdict:** Correctly renamed. Purely local Audisor error.

---

### 6. `FrozenAudisorPolicy` (class)

```yaml
verification:
  symbol: FrozenAudisorPolicy
  current_name: FrozenAudisorPolicy
  actual_behavior: |
    Immutable policy dataclass: enabled, provider, model_id, base_url, timeout.
    read_frozen_audisor_policy() calls is_aflow_enabled() to set enabled field.
    The policy represents Audisor's execution configuration, NOT A-Flow's toggle.
    A-Flow ON/OFF is read into the policy, but the policy itself is Audisor's.
  callers:
    - operation.py: read_frozen_audisor_policy()
    - audisor_run_gate.py: check_aflow_gate()
    - ignition.py: ignite()
    - plan_trigger.py: auto_trigger_plan_review()
  product_owner: audisor
  rename_correct: true
  required_action: preserve
  evidence:
    - file: openai_project/runtime/src/audisor/audisor_lifecycle/operation.py
      symbol: FrozenAudisorPolicy
    - file: openai_project/runtime/src/audisor/audisor_lifecycle/operation.py
      symbol: read_frozen_audisor_policy()
```

**Verdict:** Correctly renamed. The policy is Audisor's execution configuration. It reads A-Flow state but represents Audisor's runtime parameters.

---

### 7. `AudisorOperationContext` (class)

```yaml
verification:
  symbol: AudisorOperationContext
  current_name: AudisorOperationContext
  actual_behavior: |
    Immutable context for one Audisor operation: operation_id, type, task, plan,
    repository_context, workspace_identity, authority_context, context_sha256,
    analysis_package.
    Used by ignition, artifacts, adapter, and local invoker.
  callers:
    - operation.py: make_operation_context()
    - ignition.py: ignite(operation_context=...)
    - artifacts.py: persist_audisor_stage(context, ...)
    - adapter.py: assemble_contract()
    - local_invoker.py: invoke_audisor_analysis()
  product_owner: audisor
  rename_correct: true
  required_action: preserve
  evidence:
    - file: openai_project/runtime/src/audisor/audisor_lifecycle/operation.py
      symbol: AudisorOperationContext
```

**Verdict:** Correctly renamed. Pure Audisor operation context.

---

### 8. `AudisorInvoker` (type alias)

```yaml
verification:
  symbol: AudisorInvoker
  current_name: AudisorInvoker
  actual_behavior: |
    Callable type alias for the function that invokes Audisor analysis.
    Default is local_invoke_audisor_analysis (local worker).
    Can be injected for testing or alternative providers.
  callers:
    - ignition.py: invoke_audisor_analysis parameter
    - audisor_run_gate.py: invoke_audisor_analysis parameter
  product_owner: audisor
  rename_correct: true
  required_action: preserve
  evidence:
    - file: openai_project/runtime/src/audisor/audisor_lifecycle/ignition.py
      symbol: AudisorInvoker = Callable[..., Mapping[str, Any]]
```

**Verdict:** Correctly renamed. This is the local Audisor invocation interface.

---

### 9. `invoke_audisor_analysis` (function)

```yaml
verification:
  symbol: invoke_audisor_analysis
  current_name: invoke_audisor_analysis
  actual_behavior: |
    Host boundary for frozen Audisor analysis decision. Calls local worker,
    validates candidate schema, seals decision, returns adapter input.
    Pure local Audisor execution. Never calls aflow_review.
  callers:
    - local_invoker.py: def invoke_audisor_analysis()
    - ignition.py: callback = invoke_audisor_analysis or local_invoke...
    - audisor_run_gate.py: invoke_audisor_analysis parameter
  product_owner: audisor
  rename_correct: true
  required_action: preserve
  evidence:
    - file: openai_project/runtime/src/audisor/audisor_lifecycle/local_invoker.py
      symbol: def invoke_audisor_analysis()
```

**Verdict:** Correctly renamed. Pure local Audisor analysis function.

---

### 10. `persist_audisor_stage` (function)

```yaml
verification:
  symbol: persist_audisor_stage
  current_name: persist_audisor_stage
  actual_behavior: |
    Atomically persist one canonical stage artifact (analysis-package, raw-response,
    candidate-analysis, sealed-decision, adapter, execution-contract).
    Used by local invoker and plan trigger.
  callers:
    - artifacts.py: def persist_audisor_stage()
    - local_invoker.py: persist_audisor_stage(operation_context, ...)
    - plan_trigger.py: persist_audisor_stage(operation_context, ...)
  product_owner: audisor
  rename_correct: true
  required_action: preserve
  evidence:
    - file: openai_project/runtime/src/audisor/audisor_lifecycle/artifacts.py
      symbol: def persist_audisor_stage()
```

**Verdict:** Correctly renamed. Artifact persistence is Audisor's responsibility.

---

### 11. `audisor_run_gate.py` (module)

```yaml
verification:
  symbol: audisor_run_gate.py
  current_name: audisor_run_gate.py
  actual_behavior: |
    Host boundary for automatic Audisor gating of `audisor run` command.
    Calls ignite() when Audisor is enabled. When disabled, permits immediately.
    The gate is Audisor's CLI preflight, not A-Flow's.
  callers:
    - cli.py: from .audisor_run_gate import check_aflow_gate
  product_owner: audisor
  rename_correct: true
  required_action: preserve
  evidence:
    - file: openai_project/runtime/src/audisor/audisor_run_gate.py
      symbol: check_aflow_gate()
```

**Verdict:** Correctly renamed. This is Audisor's run gate.

---

### 12. `audisor-artifacts` (string literal)

```yaml
verification:
  symbol: audisor-artifacts
  current_name: audisor-artifacts
  actual_behavior: |
    Directory name for persisted Audisor stage artifacts.
    Written by artifacts.py, read by local_invoker.py.
  callers:
    - artifacts.py: root = Path(path) / "audisor-artifacts"
    - local_invoker.py: path = Path(...) / "audisor-artifacts" / f"{name}.json"
  product_owner: audisor
  rename_correct: true
  required_action: preserve
  evidence:
    - file: openai_project/runtime/src/audisor/audisor_lifecycle/artifacts.py
      symbol: "audisor-artifacts"
```

**Verdict:** Correctly renamed. Artifact directory for Audisor.

---

### 13. `audisor-state` (string literal)

```yaml
verification:
  symbol: audisor-state
  current_name: audisor-state
  actual_behavior: |
    Directory name for active lock and audit records.
    Written by hook.py and plan_trigger.py.
  callers:
    - hook.py: default_state_root() returns ... / "audisor-state"
    - plan_trigger.py: resolved_state_root / "audisor-state"
  product_owner: shared_adapter
  rename_correct: true
  required_action: preserve
  evidence:
    - file: openai_project/runtime/src/audisor/audisor_lifecycle/hook.py
      symbol: default_state_root()
    - file: openai_project/runtime/src/audisor/audisor_lifecycle/plan_trigger.py
      symbol: auto_trigger_plan_review()
```

**Verdict:** Correctly renamed. State directory used by both Audisor hook and A-Flow bridge. Since it's in the Audisor lifecycle package, `audisor-state` is appropriate.

---

### 14. `audisor_pretool` (string literal)

```yaml
verification:
  symbol: audisor_pretool
  current_name: audisor_pretool
  actual_behavior: |
    Hook name recorded in audit logs for PreToolUse mutation interception.
    Used exclusively by hook.py for mutation authority enforcement.
  callers:
    - hook.py: record["hook_name"] = "audisor_pretool"
  product_owner: audisor
  rename_correct: true
  required_action: preserve
  evidence:
    - file: openai_project/runtime/src/audisor/audisor_lifecycle/hook.py
      symbol: "audisor_pretool"
```

**Verdict:** Correctly renamed. This is Audisor's pre-tool hook.

---

### 15. `Audisor review: ON/OFF` → `A-Flow: ON/OFF` (CLI label)

```yaml
verification:
  symbol: "Audisor review: ON/OFF"
  current_name: "A-Flow: ON/OFF"
  actual_behavior: |
    CLI status label for the `aflow on/off/status` subcommand.
    Reads/writes is_aflow_enabled() which controls automatic A-Flow plan review.
    This is the user-facing A-Flow toggle, NOT an Audisor status.
  callers:
    - cli.py: print(f"A-Flow: {'ON' if is_aflow_enabled() else 'OFF'}")
    - test_cli.py: assert capsys.readouterr().out.strip() == "A-Flow: ON"
  product_owner: aflow
  rename_correct: false
  required_action: revert
  evidence:
    - file: openai_project/runtime/src/audisor/cli.py
      symbol: is_aflow_enabled()
    - file: openai_project/runtime/src/audisor/cli.py
      symbol: set_aflow_enabled()
```

**Verdict:** **CORRECTED.** The original rename misattributed the A-Flow toggle to Audisor. Reverted to `A-Flow: ON/OFF`.

---

### 16. `AUDISOR_CONFIG_ENV` (environment variable)

```yaml
verification:
  symbol: AUDISOR_CONFIG_ENV
  current_name: AUDISOR_CONFIG_ENV
  actual_behavior: |
    Env var pointing to Audisor configuration file. Config file contains
    aflow_enabled field (A-Flow toggle) and provider settings (Audisor runtime).
    The env var is Audisor's configuration path, which happens to include A-Flow state.
  callers:
    - config.py: AUDISOR_CONFIG_ENV = "AUDISOR_CONFIG_PATH"
    - analysis_package.py: os.environ.get(AUDISOR_CONFIG_ENV)
  product_owner: audisor
  rename_correct: true
  required_action: preserve
  evidence:
    - file: openai_project/runtime/src/audisor/config.py
      symbol: AUDISOR_CONFIG_ENV
```

**Verdict:** Correctly renamed. This is Audisor's configuration environment variable.

---

## Validation Tests

| # | Validation | Test File | Test Name | Status |
|---|---|---|---|---|
| 1 | A-Flow ON causes eligible automatic plan processing | test_audisor_auto_trigger.py | test_06_full_chain_creates_lock_when_review_passes | ✅ |
| 2 | A-Flow OFF prevents that processing | test_audisor_run_gate.py | test_aflow_off_skips_ignite_and_runs_provider | ✅ |
| 3 | Visible ON/OFF label identifies A-Flow | test_cli.py | test_audisor_commands_persist_and_report_state | ✅ (corrected) |
| 4 | Audisor can be explicitly invoked independently of A-Flow state | test_audisor_ignition.py | test_ignite_with_disabled_policy | ✅ |
| 5 | Audisor can run while A-Flow is OFF | test_audisor_run_gate.py | test_aflow_off_skips_ignite_and_runs_provider | ✅ |
| 6 | A-Flow automatic processing without Audisor identity | test_audisor_auto_trigger.py | test_02_non_mutation_task_skips_auto_trigger | ✅ |
| 7 | No `audisor_*` naming for A-Flow-only policy | Search across runtime | No matches | ✅ |
| 8 | No `aflow_*` as normal Audisor invocation interface | Search across runtime | `aflow_review` is external MCP; no `aflow_*` as Audisor interface | ✅ |

---

## Final Result

```yaml
result: rename_verified
corrections_applied:
  - cli.py: "Audisor review: ON/OFF" → "A-Flow: ON/OFF"
  - plan_trigger.py: "Audisor review/trigger/outcome" → "A-Flow review/trigger/outcome"
  - test_cli.py: assertions updated
  - test_audisor_auto_trigger.py: docstrings updated
verification_date: 2026-07-19
report_path: .snapshot/STAGE2_PRODUCT_ROLE_VERIFICATION.md