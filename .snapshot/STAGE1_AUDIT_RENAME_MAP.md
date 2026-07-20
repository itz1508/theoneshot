# Stage 1: Audit and Classification — Local Agent Rename: A-Flow → Audisor

## Repository State (Verified)

| Property | Value |
|---|---|
| Current directory | `d:\Dev\Theoneshot` |
| Repository root | `d:\Dev\Theoneshot` |
| Branch | `main` |
| HEAD | `423f5fa7ba5529ed0a158f1498dfb7e1af312440` |
| Dirty state | 24 modified files, 35 untracked files (from prior work) |

## Architecture Understanding (Verified)

### Audisor — Local Engineering Agent
Location: `openai_project/runtime/src/audisor/`

**Entrypoints:**
- **CLI**: `audisor.cli:main` → command `audisor` (subcommands: `setup`, `aflow on/off/status`, `host accept`, `codex`, `run`)
- **API**: `audisor.main:create_app()` → FastAPI title="Audisor"
- **File batch**: `audisor.file_runner:run_file_tasks()`
- **Codex adapter**: `audisor.codex.adapter:CodexAdapter`

**Core execution:**
- `audisor.workers.local:LocalWorker` — local model worker
- `audisor.builder.executor` — build execution
- `audisor.service:TaskService` — task service router

**Lifecycle management (currently `aflow_lifecycle`):**
- `audisor.aflow_lifecycle` package — host-side adapter wrapping external frozen A-Flow analysis
- Manages contracts, locks, analysis packages, artifacts, ignition, hooks, auto-trigger

### Aflow_cli — External Automatic Plan-Review Product
Location: `D:\Dev\Aflow_cli\` (separate project, NOT in this repo)

**Surfaces:**
- `mcp_server.py` — FastMCP server registering `aflow_review` tool
- `core.py` — `review_plan()` with `plan_digest="auto"` support
- `schemas/v1/` — external trigger and review schemas
- Own `cli.py`, `config.py`, `http_service.py`

**Boundary:** Local runtime does NOT import Aflow_cli directly. Calls via injectable `AflowReviewCaller` callable.

### Supporting Mechanisms (Separate from Audisor)

| Mechanism | Location | Role |
|---|---|---|
| Pre-mutation hook | `audisor.aflow_lifecycle.hook` | Enforces authority before mutation |
| Automatic trigger | `audisor.aflow_lifecycle.plan_trigger` | Detects eligible plans, calls `aflow_review` |
| Host-agent adapter | `audisor.codex.adapter` | Delegates Codex build operations |

---

## Classification of Identifiers

### Category A: Local Audisor Identity — MUST Rename

| Current | Proposed | File(s) | Risk | Action |
|---|---|---|---|---|
| `aflow_lifecycle/` (package) | `audisor_lifecycle/` | `src/audisor/aflow_lifecycle/` + all importers | **HIGH** — 58+ import refs | Rename dir, update all imports |
| `aflow_run_gate.py` (module) | `audisor_run_gate.py` | `src/audisor/aflow_run_gate.py` + importers | MEDIUM | Rename file, update imports |
| `AFlowIndicator` (class) | `AudisorIndicator` | `src/audisor/aflow_lifecycle/indicator.py` | LOW | Rename class + usages |
| `AflowGateResult` (class) | `AudisorGateResult` | `src/audisor/aflow_run_gate.py` | LOW | Rename class + usages |
| `AflowLifecycleError` (class) | `AudisorLifecycleError` | `src/audisor/aflow_lifecycle/contract.py` | MEDIUM | Rename class + all raise/catch sites |
| `AflowInvocationError` (class) | `AudisorInvocationError` | `src/audisor/aflow_lifecycle/local_invoker.py` | LOW | Rename class + usages |
| `FrozenAFlowPolicy` (class) | `FrozenAudisorPolicy` | `src/audisor/aflow_lifecycle/operation.py` | MEDIUM | Rename class + all type hints |
| `AFlowOperationContext` (class) | `AudisorOperationContext` | `src/audisor/aflow_lifecycle/operation.py` | MEDIUM | Rename class + all type hints |
| `read_frozen_aflow_policy` (function) | `read_frozen_audisor_policy` | `src/audisor/aflow_lifecycle/operation.py` | LOW | Rename + call sites |
| `AflowInvoker` (type alias) | `AudisorInvoker` | `src/audisor/aflow_lifecycle/ignition.py` | LOW | Rename + usages |
| `invoke_aflow` (function) | `invoke_audisor_analysis` | `src/audisor/aflow_lifecycle/local_invoker.py` | LOW | Rename + call sites |
| `persist_aflow_stage` (function) | `persist_audisor_stage` | `src/audisor/aflow_lifecycle/artifacts.py` | LOW | Rename + call sites |
| `aflow_operation_artifact` (function) | `audisor_operation_artifact` | `src/audisor/aflow_lifecycle/artifacts.py` | LOW | Rename + call sites |
| `aflow-artifacts` (dir name) | `audisor-artifacts` | `src/audisor/aflow_lifecycle/artifacts.py`, `local_invoker.py` | LOW | Update string literals |
| `aflow-state` (dir name) | `audisor-state` | `src/audisor/aflow_lifecycle/hook.py`, `plan_trigger.py` | LOW | Update string literals |
| `aflow_pretool` (hook name) | `audisor_pretool` | `src/audisor/aflow_lifecycle/hook.py` | LOW | Update string literal |
| `test_aflow_*.py` (test files) | `test_audisor_*.py` | `openai_project/runtime/tests/` | MEDIUM | Rename files + internal refs |
| `aflow-plan-review/` (skill dir) | `audisor-plan-review/` | `audisor/.agents/skills/` | LOW | Rename dir, update SKILL.md |
| `A-Flow` (display name in docs) | `Audisor` | `audisor/.agents/skills/aflow-plan-review/SKILL.md` | LOW | Update docs |

### Category B: Local Implementation Identifiers — Rename if Safe

| Current | Proposed | File(s) | Risk | Action |
|---|---|---|---|---|
| `requires_aflow_analysis` (function) | `requires_audisor_analysis` | `contract.py` | LOW | Local policy function; rename |
| `is_mutation_task` (function) | KEEP | `ignition.py` | LOW | Generic utility, not identity |
| `select_candidate_plan` (function) | KEEP | `ignition.py` | LOW | Generic utility |
| `_execution_ready` (function) | KEEP | `ignition.py` | LOW | Private utility |
| `evaluate_build` (function) | KEEP | `build_analysis.py` | LOW | Generic name; update docstring only |
| `assemble_contract` (function) | KEEP | `adapter.py` | LOW | Generic name |
| `verify_contract` (function) | KEEP | `adapter.py` | LOW | Generic name |
| `auto_trigger_plan_review` (function) | KEEP | `plan_trigger.py` | LOW | Generic name, no "aflow" |
| `_plan_text_to_candidate` (function) | KEEP | `plan_trigger.py` | LOW | Private utility |
| `_build_analysis_for_lock` (function) | KEEP | `plan_trigger.py` | LOW | Private utility |

### Category C: Local Compatibility Identifiers — Preserve or Migrate with Alias

| Current | Decision | File(s) | Reason |
|---|---|---|---|
| `AFLOW_CONFIG_ENV` | RENAME to `AUDISOR_CONFIG_ENV` with fallback | `config.py`, `analysis_package.py` | Local env var; add backward-compatible fallback |
| `AFLOW_SCHEMA_ROOT` | RENAME to `AUDISOR_SCHEMA_ROOT` with fallback | `analysis_package.py` | Local env var; add backward-compatible fallback |
| `AFLOW_STATE_ROOT` | RENAME to `AUDISOR_STATE_ROOT` with fallback | `hook.py` | Local env var; add backward-compatible fallback |
| `aflow_analysis_request` (field) | PRESERVE | `codex/analysis_request.py`, schemas | Serialized field in `BuildExecutionRequest`; compatibility-sensitive |

### Category D: Audisor CLI Identity — Rename

| Current | Proposed | File(s) | Risk | Action |
|---|---|---|---|
| `A-Flow: ON/OFF` (CLI output) | `Audisor review: ON/OFF` | `cli.py` | LOW | Update print strings |
| `A-Flow checking...` (spinner label) | `Audisor checking...` | `indicator.py` | LOW | Update default label |

### Category E: Host-Agent Adapter — Keep Generic

| Current | Decision | File(s) | Reason |
|---|---|---|---|
| `CodexAdapter` | KEEP | `codex/adapter.py` | Codex-specific adapter, correctly named |
| `CodexAdapterError` | KEEP | `codex/adapter.py` | Codex-specific |
| `primary_codex` (default) | KEEP | `contract.py` | Backward compatibility default |
| `agent_identity` parameter | KEEP | `plan_trigger.py` | Already agent-agnostic |

### Category F: External Aflow_cli Contract — DO NOT Rename

| Current | Decision | File(s) | Reason |
|---|---|---|---|
| `aflow_review` (MCP tool) | **PRESERVE** | `plan_trigger.py`, SKILL.md | External Aflow_cli contract |
| `AflowReviewCaller` (type alias) | **PRESERVE** | `plan_trigger.py` | Interface to external Aflow_cli |
| `Aflow_cli` (project name) | **PRESERVE** | All references | External product |
| `../Aflow_cli/` (path) | **PRESERVE** | `local_invoker.py` | External project path |
| `from aflow.storage.hashing` | **PRESERVE** | `local_invoker.py` | External package import |
| `aflow-trigger-context.schema.json` | **PRESERVE** | External schema | External Aflow_cli schema |
| `review-bundle.schema.json` | **PRESERVE** | External schema | External Aflow_cli schema |

### Category G: Automatic Aflow Policy/Trigger — DO NOT Rename to Audisor

| Current | Decision | File(s) | Reason |
|---|---|---|---|
| `is_aflow_enabled()` | **PRESERVE** | `config.py`, `operation.py` | Controls external Aflow automatic review |
| `set_aflow_enabled()` | **PRESERVE** | `config.py` | Controls external Aflow automatic review |
| `aflow_enabled` (config field) | **PRESERVE** | `config.py` | User-controlled external policy |
| `auto_trigger_plan_review` | KEEP name | `plan_trigger.py` | Bridge function; name is generic |
| `plan_trigger.py` (module) | KEEP name | `plan_trigger.py` | Generic name, not identity |

### Category H: Persisted Schema/API Values — Preserve

| Current | Decision | File(s) | Reason |
|---|---|---|---|
| `aflow_decision` (contract field) | **PRESERVE** | `adapter.py`, `contract.py` | Serialized contract field |
| `frozen_aflow_result` (adapter input) | **PRESERVE** | `adapter.py` | Serialized adapter input key |
| `FROZEN_TO_CONTRACT_READINESS` | **PRESERVE** | `contract.py`, `adapter.py` | External enum mapping |
| `FROZEN_ANALYSIS_READY` | **PRESERVE** | `contract.py` | External enum value |
| `FROZEN_FINAL_PROVEN` | **PRESERVE** | `contract.py` | External enum value |
| `no_material_gap` | **PRESERVE** | Throughout | External enum value |
| `material_gap_found` | **PRESERVE** | Throughout | External enum value |
| `analysis-request.schema.json` | **PRESERVE** | `analysis_package.py` | External schema file name |
| `plan.schema.json` | **PRESERVE** | `local_invoker.py` | External schema reference |

### Category I: Historical/Unrelated References — Preserve

| Current | Decision | File(s) | Reason |
|---|---|---|---|
| `.codex/agents/aflow.toml` | **PRESERVE** | `.codex/agents/aflow.toml` | Defines external A-Flow reviewer role, not local Audisor |
| `aflow` (Codex agent name) | **PRESERVE** | `.codex/config.toml`, `.codex/agents/aflow.toml` | External reviewer agent definition |
| `hooks = true` (feature) | **PRESERVE** | `.codex/config.toml` | Generic feature flag |

---

## Verified Gaps

| Gap | Severity | Evidence |
|---|---|---|
| No standalone `audisor` CLI subcommand for direct agent invocation (only `setup`, `aflow`, `host`, `codex`, `run`) | **Minor** | `cli.py` has no `audisor` subcommand for gap-aware work |
| `aflow` CLI subcommand controls external Aflow on/off, not local Audisor | **Minor** | `cli.py` line 95: `aflow = commands.add_parser("aflow")` — this is the external toggle, correctly preserved |
| No explicit "compatible host agent → Audisor" delegation interface beyond Codex adapter | **Major** | Only `CodexAdapter` exists; no generic `HostAgentAdapter` |
| `audisor_backend/` directory not inspected (out of scope) | **Minor** | Task scope is `openai_project/runtime` only |

---

## Rename Map Summary

### Files to Rename
1. `openai_project/runtime/src/audisor/aflow_lifecycle/` → `audisor_lifecycle/`
2. `openai_project/runtime/src/audisor/aflow_run_gate.py` → `audisor_run_gate.py`
3. `openai_project/runtime/tests/test_aflow_*.py` → `test_audisor_*.py` (8 files)
4. `audisor/.agents/skills/aflow-plan-review/` → `audisor-plan-review/`

### Classes to Rename
1. `AFlowIndicator` → `AudisorIndicator`
2. `AflowGateResult` → `AudisorGateResult`
3. `AflowLifecycleError` → `AudisorLifecycleError`
4. `AflowInvocationError` → `AudisorInvocationError`
5. `FrozenAFlowPolicy` → `FrozenAudisorPolicy`
6. `AFlowOperationContext` → `AudisorOperationContext`

### Functions/Type Aliases to Rename
1. `read_frozen_aflow_policy` → `read_frozen_audisor_policy`
2. `AflowInvoker` → `AudisorInvoker`
3. `invoke_aflow` → `invoke_audisor_analysis`
4. `persist_aflow_stage` → `persist_audisor_stage`
5. `aflow_operation_artifact` → `audisor_operation_artifact`
6. `requires_aflow_analysis` → `requires_audisor_analysis`

### String Literals to Rename
1. `aflow-artifacts` → `audisor-artifacts`
2. `aflow-state` → `audisor-state`
3. `aflow_pretool` → `audisor_pretool`
4. `A-Flow: ON/OFF` → `Audisor review: ON/OFF`
5. `A-Flow checking...` → `Audisor checking...`

### Environment Variables to Rename (with fallback)
1. `AFLOW_CONFIG_ENV` → `AUDISOR_CONFIG_ENV` (fallback to `AFLOW_CONFIG_ENV`)
2. `AFLOW_SCHEMA_ROOT` → `AUDISOR_SCHEMA_ROOT` (fallback to `AFLOW_SCHEMA_ROOT`)
3. `AFLOW_STATE_ROOT` → `AUDISOR_STATE_ROOT` (fallback to `AFLOW_STATE_ROOT`)

### Preserved Identifiers (External Contract)
- `aflow_review`, `AflowReviewCaller`, `Aflow_cli`, `is_aflow_enabled()`, `set_aflow_enabled()`, `aflow_enabled`, all `FROZEN_*` constants, `.codex/agents/aflow.toml`

---

## Architecture Questions Answered

1. **Authoritative Audisor execution entrypoint?** `audisor.cli:main` (CLI) and `audisor.main:create_app()` (API)
2. **What code currently represents the local agent?** `audisor` package in `openai_project/runtime/src/audisor/`
3. **Can another compatible agent explicitly call Audisor today?** Only via `CodexAdapter`; no generic host-agent adapter exists → **GAP**
4. **What tool/API/MCP/adapter provides that invocation?** `CodexAdapter` in `audisor.codex.adapter`
5. **Can a user run Audisor independently through a CLI today?** Yes: `audisor setup`, `audisor run`, `audisor host accept`
6. **Authoritative CLI entrypoint?** `audisor.cli:main` registered as `audisor` script in pyproject.toml
7. **Do delegated mode and CLI mode share the same execution core?** Partially — `audisor run` uses `run_file_tasks()` + `TaskService`; Codex mode uses `CodexAdapter` + `launch_codex`. Both use `ignite()` for A-Flow gating.
8. **Is any core Audisor logic tied to Codex?** The `aflow_run_gate` and `ignition` paths are generic; only `CodexAdapter` is Codex-specific.
9. **Is Audisor incorrectly represented as a plan detector or trigger?** No — the local agent is correctly represented as a runtime. The `plan_trigger.py` is a bridge, not the agent identity.
10. **Is `aflow_review` incorrectly represented as the Audisor invocation interface?** No — `aflow_review` is correctly identified as the external Aflow_cli MCP tool in SKILL.md and plan_trigger.py.
11. **What code owns user-controlled Aflow enable/disable behavior?** `audisor.config:is_aflow_enabled()` / `set_aflow_enabled()` — correctly preserved as external control.
12. **What code automatically invokes Aflow_cli?** `audisor.aflow_lifecycle.plan_trigger:auto_trigger_plan_review()` — correctly a bridge, not the agent.
13. **What proves disabled Aflow does not run?** `ignition.py` line 79: `if not selected_policy.enabled: return IgnitionResult(False, ...)` and `aflow_run_gate.py` line 75-76.
14. **Can Audisor run without invoking Aflow_cli?** Yes — when `aflow_enabled=False`, the lifecycle is skipped.
15. **Can Aflow automatic review run without invoking Audisor?** Yes — Aflow_cli is a separate project with its own MCP server.
16. **Is there any duplicate review or execution path?** No — single `ignite()` path with clear branches.
17. **Which local `aflow_*` identifiers should become `audisor_*`?** See Category A and B above.
18. **Which Aflow identifiers must remain unchanged?** See Categories F, G, H above.

---

## Stage 1 Conclusion

**Status:** `READY_FOR_STAGE_2`

The audit confirms:
- The local agent is the `audisor` package in `openai_project/runtime/src/audisor/`
- The external Aflow_cli is at `D:\Dev\Aflow_cli\` and must not be modified
- The rename is primarily an identity alignment, not an execution model redesign
- All external contract identifiers are classified and preserved
- All local identity identifiers are classified and mapped for rename
- Import traceability is complete (58+ references identified)
- No blocking architecture mismatches found