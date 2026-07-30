# API Reference

> Auto-generated from source code on 2026-07-27. Do not edit manually.

## Overview

| | |
|---|---|
| **Base URL** | `http://localhost:8000` |
| **Total endpoints** | 19 |
| **Auth** | Bearer token (global, except `/v1/assistant/health`) |
| **Tags** | operations, assistant, chat, aflow-management, usage |

---

## Operations

Operation lifecycle management — create, track, cancel, resume, claim, and stream events.

### `POST /v1/operations`

Create a new operation.

**Request body** (`application/json`):

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `prompt` | string | Yes | Task description (min 1 char) |
| `source_kind` | string | No | One of: `task`, `prepared_plan`, `fix`. Default: `task` |
| `plan` | object \| null | No | Pre-computed plan for `prepared_plan` source |

**Response** (`200`):

| Field | Type | Description |
|-------|------|-------------|
| `operation_id` | string | Unique operation identifier |
| `state` | string | Current state |
| `detail` | object | State-specific metadata |

---

### `GET /v1/operations/{operation_id}/status`

Get the current status of an operation.

**Parameters:**

| Name | In | Type | Required | Description |
|------|----|------|----------|-------------|
| `operation_id` | path | string | Yes | Operation identifier |

**Response** (`200`): Same shape as create.

**Errors:** `404` — operation not found.

---

### `POST /v1/operations/{operation_id}/cancel`

Cancel an operation by user request.

**Parameters:**

| Name | In | Type | Required | Description |
|------|----|------|----------|-------------|
| `operation_id` | path | string | Yes | Operation identifier |

**Response** (`200`): Same shape as create.

**Errors:** `404` — operation not found.

---

### `POST /v1/operations/{operation_id}/resume`

Resume a suspended operation (e.g. after tool approval or tool result submission).

**Parameters:**

| Name | In | Type | Required | Description |
|------|----|------|----------|-------------|
| `operation_id` | path | string | Yes | Operation identifier |

**Request body** (`application/json`):

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `resume_type` | string | Yes | Type of resume (e.g. `approval_decision`, `tool_result`) |
| `suspension_id` | string | Yes | Suspension to resume from |
| `operation_version` | integer | Yes | Expected operation version for optimistic concurrency |
| `payload` | object | Yes | Resume-specific data (approval decision, tool output, etc.) |

**Response** (`200`): Same shape as create.

**Errors:** `404` — not found, `409` — conflict.

---

### `POST /v1/operations/{operation_id}/claim`

Atomically claim a pending tool execution. First valid claim wins; subsequent
claims from the same claimant are idempotent; claims from different claimants
are rejected.

**Parameters:**

| Name | In | Type | Required | Description |
|------|----|------|----------|-------------|
| `operation_id` | path | string | Yes | Operation identifier |

**Request body** (`application/json`):

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `claimant_id` | string | Yes | Unique tab/claimant identifier |
| `suspension_id` | string | Yes | Suspension to claim |
| `call_id` | string | Yes | Tool call identifier |
| `argument_digest` | string | Yes | SHA-256 of `call_id + tool_name + canonical JSON arguments` |

**Response** (`200`):

| Field | Type | Description |
|-------|------|-------------|
| `operation_id` | string | Operation identifier |
| `state` | string | Current state |
| `claimed` | boolean | Whether the claim was accepted |
| `claimant_id` | string | The successful claimant |
| `call_id` | string | Tool call identifier |
| `tool_name` | string | Name of the tool to execute |
| `arguments` | object | Bound tool arguments |
| `argument_digest` | string | Verified digest |
| `suspension_id` | string | Suspension identifier |
| `idempotent` | boolean | True if this was a replay of an existing claim |

**Errors:** `404` — not found, `409` — competing claim.

---

### `GET /v1/operations/{operation_id}/events`

Read operation events after a cursor position.

**Parameters:**

| Name | In | Type | Required | Description |
|------|----|------|----------|-------------|
| `operation_id` | path | string | Yes | Operation identifier |
| `after` | query | integer | No | Sequence number to read after (default: 0, min: 0) |
| `limit` | query | integer | No | Max events to return (default: 50, 1–100) |

**Response** (`200`):

| Field | Type | Description |
|-------|------|-------------|
| `operation_id` | string | Operation identifier |
| `events` | array | Event objects |
| `cursor` | integer | Last event sequence number |
| `terminal` | boolean | True if operation is in a terminal state |

**Errors:** `404` — not found.

---

## Assistant

One-shot assistant completions, model listing, and health.

### `POST /v1/assistant/requests`

Submit a one-shot assistant request (e.g. fix wording, expand, summarize).

**Request body** (`application/json`):

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `request_id` | string | Yes | Client-generated request ID |
| `mode` | string | Yes | One of: `fix_wording`, `expand`, `summarize`, `custom` |
| `text` | string | Yes | Input text (1–20,000 chars) |
| `selected_text` | string \| null | No | Selected sub-text for targeted fix |
| `context` | string \| null | No | Additional context (max 5,000 chars) |
| `tone` | string \| null | No | Desired tone (max 200 chars) |
| `workspace_id` | string \| null | No | Workspace identifier |
| `model` | string \| null | No | Model override within configured provider |

**Response** (`200`):

| Field | Type | Description |
|-------|------|-------------|
| `request_id` | string | Echoed request ID |
| `mode` | string | Processing mode |
| `status` | string | One of: `completed`, `uncertainty`, `failed` |
| `result` | object | Mode-specific result payload |
| `warnings` | string[] | Non-fatal warnings |
| `provider` | ProviderInfo \| null | Provider that served the request |
| `usage` | object \| null | Token usage counts |
| `engine` | string \| null | `"model"` or `"languagetool"` |
| `fallback_used` | boolean | Whether fallback was used |
| `fallback_reason` | string | Reason fallback was used (empty if not) |
| `uncertainty` | string[] | Uncertainty markers |
| `accounting` | object \| null | Usage-accounting evidence (null when not wired) |

---

### `GET /v1/assistant/models`

List selectable models for the configured provider.

**Response** (`200`):

| Field | Type | Description |
|-------|------|-------------|
| `provider` | ProviderInfo | Active provider |
| `current_model` | string | Currently selected model |
| `available_models` | string[] | All selectable models |
| `reachable` | boolean \| null | Whether the provider was probed successfully |

---

### `GET /v1/assistant/health`

⚠ Liveness probe — **no authentication required**.

**Response** (`200`):

| Field | Type | Description |
|-------|------|-------------|
| `status` | string | Always `"ok"` |
| `provider` | ProviderInfo \| null | Provider identity if configured |

---

## Chat

Operator chat with multi-turn tool-calling orchestration. Supports frontend
tool execution and operator approval flows.

### `POST /v1/chat`

Send a chat message. Returns a final reply (200), pending tool calls (202),
an approval request (202), or a provider error (503).

**Request body** (`application/json`):

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `message` | string | Yes | Current user turn (1–20,000 chars) |
| `model` | string \| null | No | Model override |
| `history` | ChatHistoryMessage[] | No | Prior completed turns (max 200) |
| `workspace_available` | boolean | No | True when WebContainer is ready (default: false) |

**History contract:** Messages must alternate user/assistant, start with user,
end with assistant (if non-empty). Two consecutive same-role messages are
rejected with 422.

**Response** (`200` — ChatResponse):

| Field | Type | Description |
|-------|------|-------------|
| `reply` | string | Model reply text |
| `provider` | ProviderInfo | Serving provider |
| `model` | string | Effective model used |
| `usage` | ChatUsage | Token usage |
| `tool_trace` | ToolCallEventResponse[] \| null | Tools called during this turn |

**Response** (`202` — ChatToolCallsPending):

| Field | Type | Description |
|-------|------|-------------|
| `turn_id` | string | Turn identifier for continuation |
| `pending_calls` | ToolCallEventResponse[] | Tool calls awaiting frontend execution |
| `completed_calls` | ToolCallEventResponse[] | Tool calls already completed |
| `loop_iteration` | integer | Current loop iteration |
| `max_loops` | integer | Maximum loop iterations allowed |

**Response** (`202` — ChatApprovalRequired):

| Field | Type | Description |
|-------|------|-------------|
| `turn_id` | string | Turn identifier |
| `tool_call` | ToolCallEventResponse | The tool call awaiting approval |
| `reason` | string | Why approval is needed |
| `risk_level` | string | One of: `low`, `medium`, `high` |

**Errors:** `422` — validation, `503` — provider unavailable.

---

### `POST /v1/chat/continue`

Resume a chat turn after frontend tool execution or approval.

**Request body** (`application/json`):

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `turn_id` | string | Yes | Turn to resume (1–100 chars) |
| `tool_results` | ToolResultSubmission[] | Yes | Results (max 50) |

**ToolResultSubmission:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `call_id` | string | Yes | Tool call identifier |
| `tool_name` | string | Yes | Tool name |
| `output` | string \| null | No | Tool output (max 200,000 chars) |
| `error` | string \| null | No | Error message (max 10,000 chars) |
| `status` | string | Yes | One of: `success`, `error`, `timeout`, `cancelled`, `approved`, `denied` |

**Response:** Same shape as `POST /v1/chat`.

---

### `POST /v1/chat/estimate`

Preview token cost for the next chat request. Never calls the provider —
approximate estimation only.

**Request body** (`application/json`):

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `message` | string | No | Draft message (default: `""`) |
| `model` | string \| null | No | Model override |
| `history` | ChatHistoryMessage[] | No | Prior turns |

**Response** (`200`):

| Field | Type | Description |
|-------|------|-------------|
| `estimated_input_tokens` | integer | Estimated input token count |
| `reserved_output_tokens` | integer | Reserved output budget |
| `context_limit` | integer \| null | Model context window (null if unknown) |
| `usable_input_tokens` | integer \| null | `context_limit - reserved_output_tokens` |
| `model` | string | Effective model |
| `method` | string | Estimation method used |
| `confidence` | string | Confidence level |

---

## A-Flow Management

A-Flow issue tracking, provider status, and retry management.

### `GET /v1/aflow/status`

Get A-Flow provider status without probing connectivity.

**Response** (`200`): Provider status object.

---

### `POST /v1/aflow/provider-probe`

Probe the A-Flow provider to verify connectivity.

**Response** (`200`): Provider probe result.

---

### `GET /v1/aflow/issues`

List A-Flow issues with pagination.

**Parameters:**

| Name | In | Type | Required | Description |
|------|----|------|----------|-------------|
| `cursor` | query | string | No | Pagination cursor |
| `limit` | query | integer | No | Max issues (default: 50, 1–100) |

**Response** (`200`): Paginated issue list.

---

### `GET /v1/aflow/issues/{issue_id}`

Get a single A-Flow issue by ID.

**Errors:** `404` — issue not found.

---

### `POST /v1/aflow/issues/{issue_id}/retry-local`

Retry an issue against the local provider.

**Request body** (`application/json`):

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `operation_version` | integer | Yes | Expected version (min: 0) |
| `idempotency_key` | string | Yes | Idempotency guard (8–128 chars) |

**Errors:** `404` — issue not found, `409` — retry not supported.

---

### `POST /v1/aflow/issues/{issue_id}/continue-with-fallback`

Retry an issue against the fallback provider (Fireworks).

**Request body:** Same as retry-local.

**Errors:** `404` — issue not found, `409` — fallback not ready.

---

## Usage

Token and capacity estimation without provider calls.

### `POST /v1/usage/estimate`

Canonical token estimation endpoint. Uses approximate estimation plus cached
model metadata — never calls the completion provider.

**Request body** (`application/json`):

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `message` | string | No | Draft message (default: `""`) |
| `model` | string \| null | No | Model override |
| `history` | object[] | No | Prior turns |

**Response** (`200`): Same shape as `/v1/chat/estimate`.

**Errors:** `503` — provider unavailable.

---

## Shared Types

### ProviderInfo

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Provider identifier |
| `source` | string | `"local"` or `"cloud"` |

### ChatHistoryMessage

| Field | Type | Description |
|-------|------|-------------|
| `role` | string | One of: `user`, `assistant` |
| `content` | string | Message content (1–20,000 chars) |

### ToolCallEventResponse

| Field | Type | Description |
|-------|------|-------------|
| `call_id` | string | Tool call identifier |
| `tool_name` | string | Tool name |
| `arguments` | object | Tool arguments |
| `executor` | string | One of: `frontend`, `backend` |
| `status` | string | One of: `pending`, `completed`, `failed` |
| `turn_id` | string | Owning turn |
| `operation_id` | string \| null | Linked operation (if any) |
| `output` | string \| null | Tool output |
| `error` | string \| null | Error message |
| `duration_ms` | integer \| null | Execution duration |

### ChatUsage

| Field | Type | Description |
|-------|------|-------------|
| `input_tokens` | integer | Prompt tokens |
| `output_tokens` | integer | Completion tokens |
| `total_tokens` | integer | Total tokens |
| `cost` | number \| null | Estimated cost (null if unknown) |
| `provider_type` | string | One of: `local`, `cloud` |
