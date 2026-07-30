# Audisor Bug Scan — Duplication & Overlap Report

**Date:** 2026-07-27
**Scope:** Full repository — `openai_project/`, `web/`, `webcontainer/`
**Method:** Manual pattern search across all Python and TypeScript sources

---

## Executive Summary

| Severity | Count | Action Required |
|----------|-------|-----------------|
| **Critical** | 1 | Tool schemas diverged between two packages — LLM sees different schemas |
| **Major** | 3 | Dual chat lifecycles, dual providers, dead code |
| **Minor** | 3 | Port hardcoding, auth header case, description drift |
| **Informational** | 2 | Fixture references, test coverage overlap |

---

## Finding 1 — CRITICAL: Tool Schema Duplication with Divergent Definitions

**Two independent tool definition sources exist for the same tools.**

| Source | Path | Schema Format |
|--------|------|---------------|
| `assistant_backend` | `tools/definitions/file_read.py` etc. | `ToolDefinition` dataclass with `executor`, `requires_approval`, `read_only`, `additionalProperties: False` |
| `operation_controller` | `tool_definitions.py` | Plain `dict` list — no executor metadata, no `additionalProperties`, no `read_only` |

**Concrete divergences for `file_read`:**

| Field | `assistant_backend` | `operation_controller` |
|-------|---------------------|------------------------|
| `description` | "Read the contents of a file from the active project workspace. Returns the file content as text. Use this to inspect source code, configuration files, test files, or any text file in the project." | "Read the contents of a file at the given path. Returns the file text." |
| `additionalProperties` | `False` (strict) | Not set (permissive) |
| `path.description` | "Relative path to the file within the workspace root. Use forward slashes (e.g. 'src/index.ts', 'package.json')." | "Relative path to the file to read." |

**Impact:** The LLM receives different tool schemas depending on which code path is active. The `operation_controller` path (the new operations lifecycle) sends permissive schemas that allow arbitrary extra parameters. The `assistant_backend` path (the old chat lifecycle) sends strict schemas with `additionalProperties: False`. This can cause:
- The model to hallucinate extra parameters that the operation_controller accepts but shouldn't
- Inconsistent behavior between the two lifecycles
- Schema validation gaps in the new path

**Files involved:**
- `openai_project/assistant_backend/src/audisor_assistant/tools/definitions/file_read.py`
- `openai_project/assistant_backend/src/audisor_assistant/tools/definitions/list_directory.py`
- `openai_project/assistant_backend/src/audisor_assistant/tools/definitions/shell_exec.py`
- `openai_project/assistant_backend/src/audisor_assistant/tools/definitions/audisor_scan.py`
- `openai_project/operation_controller/src/operation_controller/tool_definitions.py`

**Recommendation:** Establish a single source of truth. Either:
1. Move `tool_definitions.py` into a shared package that both import, or
2. Have `operation_controller` import from `assistant_backend.tools`, or
3. Generate both from a single JSON schema definition

---

## Finding 2 — MAJOR: Dual Chat Lifecycles Running in Parallel

**Two complete tool-calling lifecycles are mounted on the same server:**

| Lifecycle | Route | Orchestrator | Provider |
|-----------|-------|--------------|----------|
| Old (chat) | `POST /v1/chat/requests` | `ChatOrchestrator` in `chat_orchestrator.py` | `LocalOpenAICompatibleProvider` |
| New (operations) | `POST /v1/operations` | `OperationController` in `controller.py` | `OllamaToolLoopProvider` |

Both are mounted in `main.py`:
```python
app.include_router(router)          # old chat
app.include_router(chat_router)     # old chat (legacy prefix)
app.include_router(operations_router)  # new operations
```

**Overlap areas:**
- Both implement tool-calling loops with suspension/resume
- Both handle frontend tool routing (file_read → frontend, audisor_scan → backend)
- Both parse tool calls from LLM responses
- Both manage conversation history
- Both call the same Ollama endpoint

**Key difference:** The old lifecycle uses `LocalOpenAICompatibleProvider` which does NOT have the text-based tool call fallback parser. The new lifecycle uses `OllamaToolLoopProvider` which DOES have it. This means the old `/v1/chat` path will silently fail with quantized models that output tool calls as JSON text.

**Files involved:**
- `openai_project/assistant_backend/src/audisor_assistant/api/routes.py`
- `openai_project/assistant_backend/src/audisor_assistant/api/operations_routes.py`
- `openai_project/assistant_backend/src/audisor_assistant/application/chat_orchestrator.py`
- `openai_project/operation_controller/src/operation_controller/controller.py`
- `openai_project/assistant_backend/src/audisor_assistant/main.py`

**Recommendation:** Deprecate the old `/v1/chat` lifecycle. The new `/v1/operations` path is the canonical one. Either remove the old routes or gate them behind a feature flag.

---

## Finding 3 — MAJOR: Dual Provider Layer Calling Same Endpoint

**Two separate providers both call `http://127.0.0.1:11434/v1/chat/completions`:**

| Provider | Package | Used By | Text Fallback Parser |
|----------|---------|---------|---------------------|
| `LocalOpenAICompatibleProvider` | `assistant_backend` | Old chat lifecycle | **NO** |
| `OllamaToolLoopProvider` | `operation_controller` | New operations lifecycle | **YES** |

**Duplicate logic:**
- HTTP POST to `/v1/chat/completions`
- Request body construction (model, messages, tools, tool_choice)
- Response parsing (tool_calls extraction)
- Timeout handling
- Error normalization

**Missing capability in old provider:**
`LocalOpenAICompatibleProvider` (line 73-130 of `local_openai_compatible.py`) does NOT parse text-based tool calls. If the old chat lifecycle is used with a quantized model, tool calls will be silently lost — the model's text output will be treated as a regular response, not as a tool invocation.

**Files involved:**
- `openai_project/assistant_backend/src/audisor_assistant/providers/local_openai_compatible.py`
- `openai_project/operation_controller/src/operation_controller/providers/ollama.py`

**Recommendation:** If the old lifecycle is kept, port the text-based fallback parser to `LocalOpenAICompatibleProvider`. If deprecated (see Finding 2), this resolves automatically.

---

## Finding 4 — MINOR: Frontend Dead Code — BackendChatSource

**`BackendChatSource` is fully implemented and tested but no longer used in production.**

| Reference | Usage |
|-----------|-------|
| `App.tsx` | Uses `OperationEventSource` exclusively |
| `taskStore.ts` | Imports `OperationEventSource` only |
| `result-contract.test.tsx` | Tests `BackendChatSource` contract (lines 339-383) |
| `worker-components.test.tsx` | Tests that components DON'T import `BackendChatSource` |
| `TaskEventSource.ts` | Documents `BackendChatSource` as alternative |
| `toolExecutor.ts` | Comment references `BackendChatSource` |

`BackendChatSource` (452 lines) is a complete implementation that:
- POSTs to `/v1/chat/requests`
- Polls for tool calls
- Manages conversation history
- Handles suspension/resume

This is ~450 lines of dead code that duplicates functionality now in `OperationEventSource` (497 lines).

**Files involved:**
- `web/src/agent/BackendChatSource.ts` (dead code)
- `web/src/tests/result-contract.test.tsx` (tests dead code)
- `web/src/agent/TaskEventSource.ts` (documents dead code)

**Recommendation:** Remove `BackendChatSource.ts` and its tests once the old `/v1/chat` lifecycle is deprecated. Until then, keep it as a fallback.

---

## Finding 5 — MINOR: Port Hardcoding Inconsistency

| Location | Port | Context |
|----------|------|---------|
| `assistant_backend/main.py:122` | 8799 | Default uvicorn port |
| `web/vite.config.ts` | 8803 | Vite proxy target |
| `assistant_backend/_resume_op.py` | 8803 | Acceptance test script |
| `assistant_backend/_resume_exec.py` | 8803 | Acceptance test script |
| `assistant_backend/_check_events.py` | 8803 | Acceptance test script |
| `webcontainer/fixtures/postbuild.fixture.json` | 8799 | Fixture reference |
| `webcontainer/fixtures/webruntime-postbuild.fixture.json` | 8799 | Fixture reference |
| `webcontainer/README.md` | 8799 | Documentation |
| `assistant_backend/README.md` | 8799 | Documentation |

**Issue:** The backend defaults to 8799 but all acceptance scripts and the Vite proxy use 8803. There's no environment variable for port configuration. Documentation references the old port.

**Recommendation:** Add `--port` CLI argument or `AUDISOR_PORT` env var. Update documentation and fixtures to match.

---

## Finding 6 — MINOR: Auth Header Case Inconsistency

| Location | Header Value |
|----------|-------------|
| Backend (`auth/development.py`) | `x-audisor-dev-user` (lowercase) |
| Frontend (`OperationEventSource.ts`) | `X-Audisor-Dev-User` (title-case) |
| Frontend (`BackendChatSource.ts`) | `X-Audisor-Dev-User` (title-case) |
| Frontend (`assistantApi.ts`) | `x-audisor-dev-user` (lowercase) |
| CORS config (`main.py`) | `x-audisor-dev-user` (lowercase) |

**Impact:** HTTP headers are case-insensitive per RFC 7230, so this works. But it's inconsistent and could cause confusion during debugging.

**Recommendation:** Standardize on lowercase `x-audisor-dev-user` across the frontend.

---

## Finding 7 — INFORMATIONAL: WebContainer Fixture Port References

`webcontainer/fixtures/postbuild.fixture.json` and `webcontainer/fixtures/webruntime-postbuild.fixture.json` reference `http://127.0.0.1:8799`. These are test fixtures and don't affect runtime, but they document the old port.

---

## Finding 8 — INFORMATIONAL: Test Coverage Overlap

Both `test_operations_api.py` (285 tests) and `test_e2e_operation.py` (60 tests) test tool suspension/resume. The operations API tests use `SuspendingProvider` while the e2e tests use `ScriptedProvider`. Both verify the same state transitions. This is acceptable redundancy but could be consolidated.

---

## Summary Matrix

| # | Finding | Severity | Effort to Fix | Risk if Unfixed |
|---|---------|----------|---------------|-----------------|
| 1 | Tool schema duplication | **CRITICAL** | Medium | LLM receives inconsistent schemas; validation gaps |
| 2 | Dual chat lifecycles | MAJOR | High | Maintenance burden; old path broken with quantized models |
| 3 | Dual provider layer | MAJOR | Low (if #2 fixed) | Old path silently loses tool calls |
| 4 | BackendChatSource dead code | MINOR | Low | ~450 lines of unused code; test maintenance |
| 5 | Port hardcoding | MINOR | Low | Confusion during deployment/debugging |
| 6 | Auth header case | MINOR | Low | Debugging confusion |
| 7 | Fixture port references | INFO | Trivial | Documentation drift |
| 8 | Test overlap | INFO | Low | Acceptable redundancy |

---

## Recommended Action Order

1. **Fix tool schema duplication** — establish single source of truth (Finding 1)
2. **Deprecate old `/v1/chat` lifecycle** — gate or remove (Finding 2)
3. **Remove `BackendChatSource`** — once old lifecycle is gone (Finding 4)
4. **Standardize port configuration** — add env var, update docs (Finding 5)
5. **Normalize auth header case** — lowercase everywhere (Finding 6)
6. **Update fixture references** — match new port (Finding 7)
