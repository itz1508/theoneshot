# Authority & Migration Audit — `_bug_scan_report.md`

**Date:** 2026-07-27
**Method:** Read-only trace from `App.tsx` through HTTP to provider/controller; field-by-field schema comparison; provider diff; import graph for dead-code claim.

---

## Verified Architecture Map

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                              BROWSER (Vite :5173)                               │
│                                                                                 │
│  App.tsx                                                                        │
│  ├── railTab !== 'assistant' && !== 'webruntime'                               │
│  │   └── MessageComposer → taskStore.sendMessage()                              │
│  │       └── _eventSource.start()                                               │
│  │           └── OperationEventSource (singleton)                               │
│  │               ├── POST /v1/operations ─────────────────────────────────┐     │
│  │               ├── GET  /v1/operations/{id}/events ────────────────────┤     │
│  │               ├── POST /v1/operations/{id}/resume ────────────────────┤     │
│  │               └── POST /v1/operations/{id}/cancel ────────────────────┤     │
│  │                                                                        │     │
│  ├── railTab === 'assistant'                                              │     │
│  │   └── WritingDesignAssistant                                           │     │
│  │       └── assistantApi.ts                                              │     │
│  │           ├── POST /v1/assistant/requests ────────────────────────┐    │     │
│  │           └── GET  /v1/assistant/models ──────────────────────┐   │    │     │
│  │                                                                │   │    │     │
│  └── chatCapacity.ts (always running for meter)                   │   │    │     │
│      └── POST /v1/chat/estimate ─────────────────────────────┐   │   │    │     │
│                                                               │   │   │    │     │
│  ╔══════════════════════════════════════════════════╗         │   │   │    │     │
│  ║ BackendChatSource.ts — NOT instantiated in App   ║         │   │   │    │     │
│  ║   POST /v1/chat ──────────────────────────── ✘   ║         │   │   │    │     │
│  ║   POST /v1/chat/continue ─────────────────── ✘   ║         │   │   │    │     │
│  ╚══════════════════════════════════════════════════╝         │   │   │    │     │
├───────────────────────────────────────────────────────────────┼───┼───┼────┼─────┤
│                     BACKEND (uvicorn :8803)                    │   │   │    │     │
│                                                               │   │   │    │     │
│  operations_router (/v1/operations) ◄─────────────────────────┘   │   │    │     │
│  └── _get_controller()                                            │   │    │     │
│      └── OperationController                                      │   │    │     │
│          ├── LLMPlanningAdapter ──┐                               │   │    │     │
│          ├── StubReviewAdapter    │── OllamaToolLoopProvider      │   │    │     │
│          └── LLMExecutionAdapter ─┘   (text fallback parser ✓)    │   │    │     │
│              tool_definitions: STANDARD_TOOL_DEFINITIONS           │   │    │     │
│              (plain dicts, no additionalProperties)                │   │    │     │
│                                                                    │   │    │     │
│  chat_router (/v1/chat) ◄─────────────────────────────────────────┘   │    │     │
│  └── ChatOrchestrator(registry=default_registry)                      │    │     │
│      └── LocalOpenAICompatibleProvider                                │    │     │
│          (NO text fallback parser ✗)                                  │    │     │
│          tool schemas: default_registry → ToolDefinition objects      │    │     │
│          (with additionalProperties: False)                           │    │     │
│                                                                       │    │     │
│  router (/v1/assistant) ◄─────────────────────────────────────────────┘    │     │
│  └── AssistantService.handle()                                             │     │
│      └── LocalOpenAICompatibleProvider (same as chat)                      │     │
│                                                                            │     │
│  chatCapacity endpoint (/v1/chat/estimate) ◄───────────────────────────────┘     │
│  └── ApproximateTokenEstimator (no provider call)                                │
│                                                                                  │
├──────────────────────────────────────────────────────────────────────────────────┤
│                          OLLAMA (:11434)                                         │
│  /v1/chat/completions ◄── both providers call this                               │
└──────────────────────────────────────────────────────────────────────────────────┘
```

### Key: Two distinct backend surfaces, two distinct frontend consumers

| Surface | Frontend consumer | Backend route | Provider | Tool schemas |
|---------|------------------|---------------|----------|-------------|
| Coding operations | `OperationEventSource` (active) | `/v1/operations/*` | `OllamaToolLoopProvider` | `STANDARD_TOOL_DEFINITIONS` (plain dicts) |
| Operator chat | `BackendChatSource` (NOT active) | `/v1/chat`, `/v1/chat/continue` | `LocalOpenAICompatibleProvider` | `default_registry` (rich `ToolDefinition`) |
| Writing assistant | `WritingDesignAssistant` (active) | `/v1/assistant/requests` | `LocalOpenAICompatibleProvider` | N/A (no tools) |
| Token meter | `chatCapacity.ts` (active) | `/v1/chat/estimate` | None (estimator only) | N/A |

---

## §1 — Runtime Reachability Verification

### `/v1/chat` (POST)

| Trace step | Evidence |
|------------|----------|
| `App.tsx` | Line 91-92: renders `<WritingDesignAssistant />` when `railTab === 'assistant'` |
| `WritingDesignAssistant` | Uses `assistantApi.ts` → `POST /v1/assistant/requests` — **NOT** `/v1/chat` |
| `BackendChatSource.ts` | Line 138: `fetch('/v1/chat', ...)` — but **NOT instantiated** in `App.tsx` |
| `App.tsx` line 27 | `const eventSource = new OperationEventSource()` — only this source is created |
| `taskStore.sendMessage` | Line 281: `_eventSource.start(...)` — calls `OperationEventSource.start()` |

**Verdict: REACHABLE via backend route mount, but NOT reached from production UI.** The `chat_router` is mounted in `main.py:114` so any HTTP client can call it. Tests exercise it (`test_chat_endpoint.py`, `test_operator_chat_foundation.py`). The frontend `BackendChatSource` that called it is no longer instantiated.

### `/v1/chat/continue` (POST)

| Trace step | Evidence |
|------------|----------|
| `BackendChatSource.ts` | Line 367, 448: `fetch('/v1/chat/continue', ...)` |
| Production UI | No component imports or constructs `BackendChatSource` |

**Verdict: REACHABLE only via tests and direct HTTP. No production UI path calls it.**

### `/v1/operations` (POST)

| Trace step | Evidence |
|------------|----------|
| `App.tsx` line 27 | `new OperationEventSource()` |
| `App.tsx` line 53 | `bindEventSource(eventSource)` |
| `taskStore.ts` line 281 | `_eventSource.start(text, primary, linked, opts)` |
| `OperationEventSource.ts` line 150 | `fetch('/v1/operations', { method: 'POST', ... })` |
| `operations_routes.py` line 121 | `@operations_router.post("")` → `controller.accept(...)` |
| `operations_routes.py` line 82-106 | `_get_controller()` → `OperationController(OllamaToolLoopProvider, STANDARD_TOOL_DEFINITIONS)` |

**Verdict: ACTIVELY USED. This is the production coding-operation path.**

### `/v1/operations/{id}/events` (GET)

| Trace step | Evidence |
|------------|----------|
| `OperationEventSource.ts` line 233 | `fetch(\`/v1/operations/${id}/events?after=${cursor}\`)` |
| `operations_routes.py` line 139 | `@operations_router.get("/{operation_id}/events")` |

**Verdict: ACTIVELY USED. Polling path for all operation progress.**

### `/v1/operations/{id}/resume` (POST)

| Trace step | Evidence |
|------------|----------|
| `OperationEventSource.ts` line 403 | `fetch(\`/v1/operations/${id}/resume\`, ...)` (tool results) |
| `OperationEventSource.ts` line 468 | `fetch(\`/v1/operations/${id}/resume\`, ...)` (approval) |
| `operations_routes.py` line 191 | `@operations_router.post("/{operation_id}/resume")` |

**Verdict: ACTIVELY USED. Tool result and approval submission path.**

---

## §2 — Authoritative Production Lifecycle

### OperationController lifecycle (AUTHORITATIVE for coding tasks)

| Capability | Status | Evidence |
|------------|--------|----------|
| Ordinary informational chat | **NOT SUPPORTED** | OperationController is designed for governed coding operations, not Q&A chat |
| Coding-task routing | ✅ Supported | `controller.accept(source_kind, prompt, context)` |
| Real Qwen provider calls | ✅ Supported | `OllamaToolLoopProvider` with text fallback parser |
| Frontend WebContainer tool execution | ✅ Supported | `ToolLoopSuspendedForTool` → events → `OperationEventSource` executes |
| Approval | ✅ Supported | `tool_approval_requested` event → `OperationEventSource._handleApprovalRequest` |
| Cancellation | ✅ Supported | `POST /v1/operations/{id}/cancel` |
| Persistence | ✅ Supported | `EventStore` under `.codex/operations/` |
| Browser reattachment | ✅ Supported | `OperationEventSource.reattach(operationId)` |
| Terminal result rendering | ✅ Supported | `task.completed` / `task.cancelled` events → store → UI |

### ChatOrchestrator lifecycle (ACTIVE for operator chat Q&A)

| Capability | Status | Evidence |
|------------|--------|----------|
| Ordinary informational chat | ✅ Supported | `execute_turn()` with tool-calling loop |
| Coding-task routing | ⚠️ Partially | Can call tools but no governed lifecycle (no plan/review/execute phases) |
| Real Qwen provider calls | ⚠️ BROKEN with quantized models | `LocalOpenAICompatibleProvider` lacks text fallback parser |
| Frontend WebContainer tool execution | ✅ Supported | `ChatTurnPending` → 202 response → client executes |
| Approval | ✅ Supported | `ChatTurnApprovalRequired` → 202 response |
| Cancellation | ❌ Not supported | No cancel endpoint for chat turns |
| Persistence | ⚠️ In-memory only | `_turn_states` dict with TTL — lost on restart |
| Browser reattachment | ❌ Not supported | Turn state is server-memory-only |
| Terminal result rendering | ✅ Supported | `ChatTurnFinal` → 200 response with text |

### Behaviour only available through `/v1/chat`

1. **Token estimate** — `POST /v1/chat/estimate` is on `chat_router` and used by `chatCapacity.ts`. This is NOT a coding operation; it's a pre-send meter. **Must be preserved** (or moved to a neutral route).
2. **Multi-turn informational Q&A with tools** — The ChatOrchestrator supports multi-turn conversation with tool calling that doesn't enter the governed lifecycle. This is a distinct use case from coding operations.

**Critical finding:** The `/v1/chat/estimate` endpoint is actively used by the production UI for the token meter. It lives on `chat_router` but does NOT call the provider — it's a pure estimation endpoint. It must not be removed.

---

## §3 — Tool Definition Field-by-Field Comparison

### `file_read`

| Field | `assistant_backend` (registry) | `operation_controller` (STANDARD) | Classification |
|-------|-------------------------------|-----------------------------------|----------------|
| `name` | `"file_read"` | `"file_read"` | ✅ Match |
| `description` | "Read the contents of a file from the active project workspace. Returns the file content as text. Use this to inspect source code, configuration files, test files, or any text file in the project." | "Read the contents of a file at the given path. Returns the file text." | **behaviour-changing schema drift** — shorter desc gives model less guidance |
| `parameters.type` | `"object"` | `"object"` | ✅ Match |
| `parameters.properties.path.type` | `"string"` | `"string"` | ✅ Match |
| `parameters.properties.path.description` | "Relative path to the file within the workspace root. Use forward slashes (e.g. 'src/index.ts', 'package.json')." | "Relative path to the file to read." | **harmless wording drift** |
| `parameters.required` | `["path"]` | `["path"]` | ✅ Match |
| `parameters.additionalProperties` | `False` | **NOT SET** | **security/policy drift** — operation_controller allows arbitrary extra params |
| `executor` | `ToolExecutor.FRONTEND` | Not specified | **intentional runtime metadata** — operation_controller uses hardcoded `frontend_tools` set |
| `requires_approval` | `False` | Not specified | **intentional runtime metadata** |
| `read_only` | `True` | Not specified | **intentional runtime metadata** |

### `list_directory`

| Field | `assistant_backend` (registry) | `operation_controller` (STANDARD) | Classification |
|-------|-------------------------------|-----------------------------------|----------------|
| `name` | `"list_directory"` | `"list_directory"` | ✅ Match |
| `description` | "List the files and directories at a given path in the active project workspace. Returns entries with their type (file or directory). Use this to explore project structure before reading specific files." | "List the contents of a directory. Returns file and subdirectory names." | **harmless wording drift** |
| `parameters.properties.path.type` | `"string"` | `"string"` | ✅ Match |
| `parameters.properties.path.description` | "Relative path to the directory within the workspace root. Use '.' or '' for the root. Use forward slashes." | "Relative path to the directory. Use '.' for root." | **harmless wording drift** |
| `parameters.properties.path.default` | `"."` | **NOT SET** | **behaviour-changing schema drift** — model may omit `path` with registry but not with STANDARD |
| `parameters.required` | `[]` (path is optional) | `["path"]` (path is required) | **behaviour-changing schema drift** — different required-ness |
| `parameters.additionalProperties` | `False` | **NOT SET** | **security/policy drift** |
| `executor` | `ToolExecutor.FRONTEND` | Not specified | intentional |
| `requires_approval` | `False` | Not specified | intentional |
| `read_only` | `True` | Not specified | intentional |

### `shell_exec`

| Field | `assistant_backend` (registry) | `operation_controller` (STANDARD) | Classification |
|-------|-------------------------------|-----------------------------------|----------------|
| `name` | `"shell_exec"` | `"shell_exec"` | ✅ Match |
| `description` | Long (mentions WebContainer, npm examples) | Short ("Execute a shell command...") | **harmless wording drift** |
| `parameters.properties.command` | ✅ Match | ✅ Match | ✅ Match |
| `parameters.properties.timeout_seconds` | Present (integer, default 120, min 1, max 300) | **NOT PRESENT** | **behaviour-changing schema drift** — model can specify timeout in one path but not the other |
| `parameters.required` | `["command"]` | `["command"]` | ✅ Match |
| `parameters.additionalProperties` | `False` | **NOT SET** | **security/policy drift** |
| `executor` | `ToolExecutor.FRONTEND` | Not specified | intentional |
| `requires_approval` | **`True`** | Not specified | **CRITICAL: operation_controller has NO approval requirement for shell_exec** |
| `read_only` | `False` | Not specified | intentional |

### `file_write`

| Field | `assistant_backend` (registry) | `operation_controller` (STANDARD) | Classification |
|-------|-------------------------------|-----------------------------------|----------------|
| `name` | `"file_write"` | `"file_write"` | ✅ Match |
| `description` | **NOT DEFINED in assistant_backend** | "Write content to a file at the given path..." | **unknown** — file_write is NOT registered in `assistant_backend/tools/definitions/` |
| `parameters.properties.path` | N/A | ✅ Present | — |
| `parameters.properties.content` | N/A | ✅ Present | — |
| `parameters.additionalProperties` | N/A | **NOT SET** | — |
| `executor` | N/A | Not specified | — |
| `requires_approval` | N/A | Not specified | **CRITICAL: no approval for file_write in operation_controller** |

**CRITICAL FINDING:** `file_write` does NOT exist in `assistant_backend/tools/definitions/`. There is no `file_write.py` in the definitions directory. The `assistant_backend` registry has only 4 tools: `file_read`, `list_directory`, `shell_exec`, `audisor_scan`. The `operation_controller` has 5 tools including `file_write`.

### `audisor_scan`

| Field | `assistant_backend` (registry) | `operation_controller` (STANDARD) | Classification |
|-------|-------------------------------|-----------------------------------|----------------|
| `name` | `"audisor_scan"` | `"audisor_scan"` | ✅ Match |
| `description` | "Run an Audisor scan on a file or directory..." | "Run the Audisor static analysis scan on the codebase..." | **harmless wording drift** |
| `parameters.properties` | `target` (string, required), `depth` (string, enum: surface/standard/deep, default: standard) | `query` (string, required) | **behaviour-changing schema drift** — DIFFERENT parameter names and schemas |
| `parameters.required` | `["target"]` | `["query"]` | **behaviour-changing schema drift** — different param name |
| `parameters.additionalProperties` | `False` | **NOT SET** | **security/policy drift** |
| `executor` | `ToolExecutor.BACKEND` | Not specified | intentional |
| `requires_approval` | `False` | Not specified | intentional |
| `read_only` | `True` | Not specified | intentional |

**CRITICAL FINDING:** `audisor_scan` has completely different parameter schemas between the two systems. The registry uses `target` + `depth` (enum); the STANDARD uses `query` (string). The model would call this tool with different arguments depending on which schema it receives.

### Summary of schema divergences

| Tool | `additionalProperties` | Description | Required fields | Parameter names | Approval | Extra params |
|------|----------------------|-------------|-----------------|-----------------|----------|-------------|
| `file_read` | DRIFT | DRIFT | ✅ match | ✅ match | N/A | — |
| `list_directory` | DRIFT | DRIFT | **DRIFT** (optional vs required) | ✅ match | N/A | `default` value drift |
| `shell_exec` | DRIFT | DRIFT | ✅ match | ✅ match | **CRITICAL** | `timeout_seconds` missing in STANDARD |
| `file_write` | N/A (not in registry) | N/A (not in registry) | — | — | **CRITICAL** | — |
| `audisor_scan` | DRIFT | DRIFT | **DRIFT** (`target` vs `query`) | **CRITICAL** | N/A | `depth` enum missing in STANDARD |

---

## §4 — Ownership Boundary Evaluation

### Proposed model

```
shared tool-contract package
→ canonical names, argument schemas, result schemas, capability metadata

OperationController
→ lifecycle, allowed/prohibited policy, suspension, resume, approval

assistant_backend
→ backend executor bindings and provider adaptation

web frontend
→ WebContainer executor bindings
```

### Current state

| Layer | Exists? | Location |
|-------|---------|----------|
| Shared tool contract | ❌ NO | Two independent definitions |
| OperationController lifecycle | ✅ YES | `operation_controller/controller.py` |
| ChatOrchestrator lifecycle | ✅ YES | `assistant_backend/application/chat_orchestrator.py` |
| Backend executor bindings | ✅ YES | `assistant_backend/tools/registry.py` + `ToolExecutor.BACKEND` |
| Frontend executor bindings | ✅ YES | `web/src/agent/toolExecutor.ts` |
| Provider adaptation | ✅ YES | Both providers adapt Ollama's API |

### Does a suitable shared contract module already exist?

**No.** The two closest candidates are:

1. `operation_controller/tool_definitions.py` — plain dicts, no validation metadata, no executor info
2. `assistant_backend/tools/definitions/` — rich dataclass with executor, approval, read_only, but no result schemas

Neither is suitable as-is for a shared contract because:
- `tool_definitions.py` lacks `additionalProperties`, executor metadata, and approval/read_only flags
- `tools/definitions/` lacks result schemas and is tightly coupled to the registry singleton

**A new shared module is needed**, or one of the existing modules must be elevated and extended.

### Recommended boundary

```
openai_project/schemas/tool_contracts.py  (or operation_controller/tool_contracts.py)
→ CanonicalToolDefinition: name, description, parameters (with additionalProperties), 
  executor (frontend|backend), requires_approval, read_only, result_schema

OperationController
→ Reads CanonicalToolDefinition, applies allowed/prohibited policy
→ Does NOT own tool schemas

assistant_backend
→ Reads CanonicalToolDefinition for registry
→ Owns backend executor bindings (audisor_scan execution)

web frontend
→ Owns WebContainer executor bindings (file_read, file_write, list_directory, shell_exec)
→ Does NOT define tool schemas — consumes from backend events
```

---

## §5 — Provider Implementation Comparison

| Field | `LocalOpenAICompatibleProvider` | `OllamaToolLoopProvider` |
|-------|-------------------------------|--------------------------|
| **Package** | `assistant_backend/providers/` | `operation_controller/providers/` |
| **Endpoint** | `{base_url}/v1/chat/completions` | `{base_url}/v1/chat/completions` |
| **Default base_url** | `http://127.0.0.1:11434` | `http://127.0.0.1:11434` |
| **Default model** | From `AUDISOR_MODEL_ID` env (required) | `qwen2.5-coder:7b` (hardcoded fallback) |
| **Auth** | None (local) | None (local) |
| **Request: system message** | Separate `system_prompt` param | Included in `messages` list |
| **Request: tools format** | `{type: "function", function: {name, description, parameters}}` | Pre-built by `ToolLoop.build_tool_schemas()` — same format |
| **Request: tool_choice** | From `request.tool_choice` (passed through) | Always `"auto"` when tools present |
| **Request: temperature** | `0` | `0` |
| **Request: stream** | `False` | `False` |
| **Request: max_tokens** | `request.max_tokens or self.max_tokens` (default 1024) | `max_tokens` param (default 2048 from adapter) |
| **Timeout** | `request.timeout_seconds or self.timeout_seconds` (default 300) | `timeout_seconds` param |
| **Structured tool_calls parsing** | ✅ Yes — `message.get("tool_calls")` | ✅ Yes — `message.get("tool_calls")` |
| **Text/fenced JSON fallback** | ❌ **NO** | ✅ **YES** — `_try_parse_text_tool_calls()` |
| **Error: timeout** | `ProviderError(TIMEOUT)` | `ProviderResponse(finish_reason="error")` |
| **Error: connection** | `ProviderError(UNAVAILABLE)` | `ProviderResponse(finish_reason="error")` |
| **Error: HTTP status** | `ProviderError(...)` per status code | `ProviderResponse(finish_reason="error")` |
| **Error: malformed response** | `ProviderError(INVALID_RESPONSE)` | `ProviderResponse(finish_reason="error")` |
| **Usage extraction** | Full OpenAI-native sanitization (`_sanitize_native_usage`) | Basic (`prompt_tokens`, `completion_tokens`, `total_tokens`) |
| **Context window probe** | ✅ Yes — `/api/show` → `context_length` | ❌ No |
| **Model listing** | ✅ Yes — `/api/tags` | ❌ No |
| **Current callers** | `ChatOrchestrator`, `AssistantService` | `LLMPlanningAdapter`, `LLMExecutionAdapter` |
| **Tests** | Extensive (unit + integration) | Unit tests in `operation_controller/tests/` |

### Analysis

1. **Both providers call the same endpoint with the same model.** They are functionally redundant for the chat path.

2. **The text fallback parser is the critical differentiator.** Without it, quantized Qwen outputs tool calls as JSON text that `LocalOpenAICompatibleProvider` silently treats as a regular text response. This means the `/v1/chat` path is **broken for coding tasks** with quantized models.

3. **Error handling strategies differ fundamentally:**
   - `LocalOpenAICompatibleProvider` raises exceptions → caller handles
   - `OllamaToolLoopProvider` returns error responses → loop handles inline
   
   This is an architectural difference, not a bug. Both are valid patterns.

4. **The fallback parser safety concern:** The parser only fires when `not tool_calls and text.strip()` — i.e., only when the structured API returned no tool calls. It then checks if the text is valid JSON with `name` + `arguments` keys. This is safe because:
   - Normal JSON output (e.g., a plan) would need to have exactly `{"name": ..., "arguments": ...}` to be misinterpreted
   - The parser requires BOTH `name` and `arguments` keys
   - A plan JSON like `{"summary": ..., "steps": ...}` would NOT match

5. **One provider should replace the other, or both should delegate to a shared core.** The duplication is wasteful and the missing fallback parser is a real bug.

### Recommendation

**Option 2 (shared core) is safest:**

```
operation_controller/providers/ollama_core.py
→ OllamaChatCore: endpoint, request building, response parsing, fallback parser
→ Used by both OllamaToolLoopProvider and a new adapter in assistant_backend

assistant_backend/providers/local_openai_compatible.py
→ Delegates to OllamaChatCore for the actual HTTP call
→ Retains its own error normalization (ProviderError) and model listing
```

---

## §6 — BackendChatSource Dead Code Verification

### Import graph

| File | Import type | Content |
|------|-------------|---------|
| `App.tsx` | Does NOT import | Uses `OperationEventSource` exclusively |
| `taskStore.ts` | Does NOT import | References `OperationEventSource` for reattach |
| `result-contract.test.tsx` | Dynamic import in test | Tests `BackendChatSource` contract (lines 360, 382) |
| `worker-components.test.tsx` | String match in test | Asserts components do NOT import `BackendChatSource` |
| `TaskEventSource.ts` | Comment only | Documents `BackendChatSource` as alternative |
| `toolExecutor.ts` | Comment only | "Returns a result payload suitable for POST /v1/chat/continue" |

### Constructors

| Location | Constructs `BackendChatSource`? |
|----------|-------------------------------|
| `App.tsx` | ❌ No — constructs `OperationEventSource` |
| Tests | ✅ Yes — `result-contract.test.tsx` |
| Production code | ❌ **NOWHERE** |

### Exports

`BackendChatSource` is exported from its module but never imported by any production code.

### Feature flags / alternate entrypoints

No feature flags gate `BackendChatSource`. No alternate entrypoints (Storybook, demo pages, CLI) reference it.

### Does ordinary non-operation chat depend on it?

**No.** The `WritingDesignAssistant` feature uses `assistantApi.ts` → `/v1/assistant/requests`. The token meter uses `chatCapacity.ts` → `/v1/chat/estimate`. Neither uses `BackendChatSource`.

### Does approval or event mapping exist only there?

**No.** `OperationEventSource` has full approval handling (`_handleApprovalRequest`) and event mapping (`_processEvent`). The approval handler types differ (`ApprovalResolver` vs `OperationApprovalHandler`) but the functionality is equivalent.

### Classification

**`test-only` / `unreachable production code`**

`BackendChatSource` is:
- Fully implemented (494 lines)
- Tested (contract tests)
- **Not instantiated by any production code path**
- **Not reachable from the production UI**
- Functionally superseded by `OperationEventSource`

It is safe to remove, but only after:
1. The `/v1/chat` and `/v1/chat/continue` routes are either deprecated or have an alternative consumer
2. The contract tests are migrated or removed
3. The `TaskEventSource.ts` doc comment is updated

---

## §7 — Migration Graph

### Step 1: Establish canonical tool contracts

**Prerequisite:** None
**Files affected:**
- NEW: `openai_project/schemas/tool_contracts.py` (or within `operation_controller/`)
- `openai_project/operation_controller/src/operation_controller/tool_definitions.py`
- `openai_project/assistant_backend/src/audisor_assistant/tools/definitions/*.py`

**Behaviour preserved:** No runtime behaviour change — this is a schema consolidation
**Tests required:**
- Unit test: canonical definitions match current registry definitions (name, required fields)
- Unit test: `additionalProperties: False` on all canonical definitions
- Unit test: `audisor_scan` parameter name resolved (`target` vs `query`)

**Rollback boundary:** This step is purely additive — no existing code changes until Step 2

**Must not remove yet:** Nothing removed in this step

**Decisions required:**
- Resolve `audisor_scan` parameter name: `target`+`depth` (rich) vs `query` (simple)
- Resolve `list_directory` path: optional (registry) vs required (STANDARD)
- Resolve `shell_exec` `timeout_seconds`: present (registry) vs absent (STANDARD)
- Resolve `file_write` approval: not in registry at all

---

### Step 2: Make both paths consume canonical contracts

**Prerequisite:** Step 1
**Files affected:**
- `openai_project/operation_controller/src/operation_controller/tool_definitions.py` → import from canonical
- `openai_project/assistant_backend/src/audisor_assistant/tools/definitions/__init__.py` → import from canonical
- `openai_project/assistant_backend/src/audisor_assistant/tools/registry.py` → use canonical definitions

**Behaviour preserved:** Both paths send identical tool schemas to the model
**Tests required:**
- Integration test: `/v1/chat` and `/v1/operations` send identical tool schemas
- Regression: all existing tests still pass (60 + 285 + 162)

**Rollback boundary:** If tests fail, revert to pre-Step-2 imports

**Must not remove yet:** Neither definition source is removed — both now point to canonical

---

### Step 3: Unify provider behaviour

**Prerequisite:** Step 2 (tool schemas consistent)
**Files affected:**
- NEW: `openai_project/operation_controller/src/operation_controller/providers/ollama_core.py`
- `openai_project/operation_controller/src/operation_controller/providers/ollama.py` → delegate to core
- `openai_project/assistant_backend/src/audisor_assistant/providers/local_openai_compatible.py` → delegate to core for chat completions

**Behaviour preserved:** Both providers produce identical tool call parsing (including text fallback)
**Tests required:**
- Unit test: text fallback parser works when ported to shared core
- Unit test: `LocalOpenAICompatibleProvider` now handles quantized model tool calls
- Regression: all existing provider tests pass

**Rollback boundary:** Provider delegation is internal — external API unchanged

**Must not remove yet:** `LocalOpenAICompatibleProvider` retains its model listing, context window, and error normalization

---

### Step 4: Prove operation-path feature parity

**Prerequisite:** Step 3
**Files affected:** None (proof only)

**Behaviour preserved:** N/A — this is a verification step
**Tests required:**
- Live browser acceptance: full coding operation from UI through WebContainer
- Verify: ordinary informational chat still works via `/v1/assistant/requests`
- Verify: token meter still works via `/v1/chat/estimate`

**Rollback boundary:** N/A — no code changes

**Must not remove yet:** Nothing removed

---

### Step 5: Route production coding tasks exclusively through operations

**Prerequisite:** Step 4
**Files affected:**
- `web/src/App.tsx` — no change needed (already uses `OperationEventSource`)
- `web/src/store/taskStore.ts` — no change needed

**Behaviour preserved:** All coding tasks go through `OperationController`
**Tests required:**
- E2E: submit coding task from UI, verify operation lifecycle
- Verify: no code path sends coding tasks to `/v1/chat`

**Rollback boundary:** No code changes in this step

---

### Step 6: Deprecate old route visibly

**Prerequisite:** Step 5
**Files affected:**
- `openai_project/assistant_backend/src/audisor_assistant/api/routes.py` — add deprecation headers/warnings to `/v1/chat` and `/v1/chat/continue`
- `openai_project/assistant_backend/src/audisor_assistant/main.py` — keep `chat_router` mounted but log deprecation

**Behaviour preserved:** Old routes still work but emit deprecation warnings
**Tests required:**
- Verify: deprecation warning appears in logs
- Verify: all existing tests still pass

**Rollback boundary:** Deprecation is additive — routes still functional

**Must not remove yet:** Routes remain mounted; `BackendChatSource` remains in frontend

---

### Step 7: Remove old frontend/backend lifecycle only after parity tests

**Prerequisite:** Step 6 + explicit human approval
**Files affected:**
- DELETE: `web/src/agent/BackendChatSource.ts`
- EDIT: `web/src/tests/result-contract.test.tsx` — remove `BackendChatSource` contract tests
- EDIT: `web/src/agent/TaskEventSource.ts` — update doc comment
- EDIT: `web/src/agent/toolExecutor.ts` — update comment
- DECIDE: Remove or gate `/v1/chat` and `/v1/chat/continue` routes
- DECIDE: Move `/v1/chat/estimate` to neutral route (e.g., `/v1/assistant/estimate`)

**Behaviour preserved:** No production behaviour changes (BackendChatSource is unreachable)
**Tests required:**
- Full test suite: 60 + 285 + 162 all pass
- Live browser acceptance: all UI flows work
- Verify: no import of `BackendChatSource` anywhere in production code

**Rollback boundary:** Git revert of deletion commit

---

## §8 — Consolidated Output

### Claim Verification Table

| # | Finding in `_bug_scan_report.md` | Verdict | Evidence |
|---|----------------------------------|---------|----------|
| 1 | Tool schema duplication with divergent definitions | **VERIFIED** | Field-by-field comparison shows 6+ behaviour-changing differences, 4+ security/policy drifts |
| 2 | Dual chat lifecycles running in parallel | **VERIFIED** | Both `chat_router` and `operations_router` mounted in `main.py:113-115` |
| 3 | Dual provider layer calling same endpoint | **VERIFIED** | Both call `{base_url}/v1/chat/completions`; `LocalOpenAICompatibleProvider` lacks text fallback |
| 4 | Frontend dead code — BackendChatSource | **PARTIALLY VERIFIED** — classified as `test-only / unreachable production code`, not fully dead (tests exercise it) |
| 5 | Port hardcoding inconsistency | **VERIFIED** — `main.py:122` = 8799, `vite.config.ts` = 8803, fixtures = 8799 |
| 6 | Auth header case inconsistency | **VERIFIED** — backend lowercase, frontend title-case, `assistantApi.ts` lowercase |
| 7 | WebContainer fixture port references | **VERIFIED** — both fixtures reference 8799 |
| 8 | Test coverage overlap | **VERIFIED** — both test suites cover tool suspension/resume |

**Severity reassessment:**

| Finding | Original | Audited | Reason for change |
|---------|----------|---------|-------------------|
| 1 (Tool schemas) | CRITICAL | **CRITICAL** | Confirmed — `audisor_scan` has different parameter names; `additionalProperties` drift |
| 2 (Dual lifecycles) | MAJOR | **MAJOR** | Confirmed — but `/v1/chat` is NOT reached from production UI for coding tasks |
| 3 (Dual providers) | MAJOR | **MAJOR** | Confirmed — missing fallback parser is a real bug for the chat path |
| 4 (Dead code) | MINOR | **MINOR** | Confirmed — but not fully dead (tests exist); classified as test-only |

### Additional findings from audit

| # | Finding | Severity | Evidence |
|---|---------|----------|----------|
| 9 | `file_write` missing from `assistant_backend` registry | **CRITICAL** | No `file_write.py` in `tools/definitions/` — only 4 tools registered |
| 10 | `audisor_scan` parameter name mismatch | **CRITICAL** | Registry: `target`+`depth`; STANDARD: `query` — model calls with wrong params |
| 11 | `/v1/chat/estimate` must be preserved | **INFO** | `chatCapacity.ts` actively uses it for the token meter |
| 12 | `shell_exec` approval not enforced in operation_controller | **MAJOR** | Registry says `requires_approval=True`; STANDARD has no approval metadata; `tool_loop.py` does not check approval for `shell_exec` |

---

### Authority Decisions Required

These cannot be derived from repository evidence alone:

1. **Is the operator chat (informational Q&A with tools via `/v1/chat`) a supported product feature, or is it legacy?**
   - If supported: the ChatOrchestrator and its provider need the text fallback parser
   - If legacy: deprecate `/v1/chat` and `/v1/chat/continue`

2. **What is the canonical parameter name for `audisor_scan`?**
   - `target` + `depth` (rich, from registry)
   - `query` (simple, from STANDARD)

3. **Should `shell_exec` require approval in the operation path?**
   - Registry says yes; operation_controller has no approval mechanism for it
   - This is a product safety decision

4. **Should `file_write` be added to the `assistant_backend` registry?**
   - Currently only in `operation_controller` STANDARD
   - If the chat path ever needs file writing, it must be registered

5. **Where should `/v1/chat/estimate` live if `/v1/chat` is deprecated?**
   - Move to `/v1/assistant/estimate`?
   - Keep on `chat_router` but rename?

---

### Ranked Migration Plan

| Step | Title | Prerequisite | Checkpoint |
|------|-------|-------------|------------|
| 1 | Establish canonical tool contracts | Decision on `audisor_scan` params, `shell_exec` approval | Unit tests prove canonical definitions are consistent |
| 2 | Make both paths consume canonical | Step 1 | Integration test: both routes send identical schemas |
| 3 | Unify provider behaviour (port fallback parser) | Step 2 | `LocalOpenAICompatibleProvider` handles quantized models |
| 4 | Prove operation-path feature parity | Step 3 | Live browser acceptance passes |
| 5 | Route coding tasks exclusively through operations | Step 4 | E2E test: no coding tasks reach `/v1/chat` |
| 6 | Deprecate old route visibly | Step 5 | Deprecation warnings in logs |
| 7 | Remove old lifecycle after parity tests | Step 6 + human approval | Full test suite + browser acceptance pass |

---

### Success Definition

The consolidation is complete only when ALL of the following hold:

| Criterion | Current state | Target state |
|-----------|--------------|-------------|
| One authoritative coding-operation lifecycle exists | ✅ `OperationController` | ✅ Unchanged |
| One canonical tool contract exists | ❌ Two divergent sources | ✅ Single `tool_contracts.py` |
| Provider behaviour is consistent across supported paths | ❌ Fallback parser missing from chat path | ✅ Shared core with fallback |
| Production UI routes coding tasks through OperationController | ✅ Already does | ✅ Unchanged |
| Ordinary chat behaviour has an explicit supported path | ⚠️ `/v1/assistant/requests` works; `/v1/chat` is ambiguous | ✅ Decision made and documented |
| No duplicate lifecycle remains reachable unintentionally | ❌ `/v1/chat` reachable but unused | ✅ Deprecated or removed |
| All full test suites pass | ✅ 60 + 285 + 162 | ✅ Unchanged |
| Live browser acceptance passes | ⚠️ Not yet executed | ✅ Full flow proven |
