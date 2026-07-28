# Audisor Writing & Design Assistant — Backend

Provider-neutral assistant backend (`audisor_assistant`). One endpoint,
six advisory modes, no browser-controlled provider selection, no
credentials in request bodies, no raw user text in logs.

## Endpoint

`POST /v1/assistant/requests`

Request body (unknown fields rejected):

| Field | Required | Notes |
|---|---|---|
| `request_id` | yes | Client-generated identifier, ≤128 chars |
| `mode` | yes | One of the six modes below |
| `text` | yes | Primary user text, ≤20 000 chars |
| `selected_text` | no | Required by `translate_slang_jargon` |
| `context` | no | Optional surrounding context, ≤8 000 chars |
| `tone` | no | Tone hint, ≤200 chars |
| `workspace_id` | no | Opaque workspace identifier |

Modes: `fix_wording`, `draft_three_replies`, `translate_slang_jargon`,
`teach_clearly`, `expand_idea`, `visualize_design`.

Response envelope: `request_id`, `mode`,
`status` (`completed` | `uncertainty` | `failed`), `result`, `warnings`,
`provider` (`{id, source}` with `source` `local` | `cloud`), `usage`,
`uncertainty`, and — for `fix_wording` — execution provenance:
`engine` (`model` | `languagetool`), `fallback_used`, `fallback_reason`.
The envelope `engine` always matches the result's `result_kind`.
Failures use
`result.error.category` ∈ `configuration`, `unavailable`, `timeout`,
`authentication`, `rate_limited`, `invalid_response`, `unsupported`,
`internal`, always with sanitized messages. There is no silent provider
fallback: one configured provider serves the request or the request fails.

Machine-readable contracts: `openai_project/schemas/assistant/`.

## Provider configuration

Selection is server-side only, via `AUDISOR_PROVIDER`:

| Value | Adapter |
|---|---|
| `local-openai-compatible` (default) | Local OpenAI-compatible chat server (Ollama, LM Studio, vLLM, …) |
| `fake-deterministic` | Deterministic in-process fake — tests and offline demos |
| `cloud-openai-compatible` | Any OpenAI-compatible cloud endpoint; no vendor is hardcoded |

Local provider environment:

| Variable | Default | Meaning |
|---|---|---|
| `AUDISOR_BASE_URL` | `http://127.0.0.1:11434` | Base URL of the local server |
| `AUDISOR_MODEL_ID` | — (required) | Model identifier |
| `AUDISOR_TIMEOUT_SECONDS` | `300` | Request timeout |
| `AUDISOR_MAX_TOKENS` | `1024` | Completion token cap |

Cloud provider environment (no default vendor; configuration is a
deployment decision):

| Variable | Meaning |
|---|---|
| `AUDISOR_ASSISTANT_CLOUD_BASE_URL` | OpenAI-compatible base URL |
| `AUDISOR_ASSISTANT_CLOUD_MODEL_ID` | Model identifier |
| `AUDISOR_ASSISTANT_CLOUD_API_KEY` | API key, read server-side at request time only |

Missing or unknown provider configuration produces a normalized
`configuration` failure envelope, never an HTTP 500 and never a fallback.

## Fix-wording engine (LanguageTool integration)

Only `fix_wording` routes through an engine selector; the other five
modes always use the configured model provider and are unaffected by
this configuration.

| Variable | Default | Values | Meaning |
|---|---|---|---|
| `AUDISOR_FIX_ENGINE` | `model` | `model` \| `languagetool` \| `auto` | Which engine serves `fix_wording` |
| `AUDISOR_FIX_FALLBACK` | `none` | `none` \| `model` | Fallback when LanguageTool is unavailable (`auto` only) |

Behavior matrix (the backend **always** starts; a LanguageTool failure
disables `fix_wording` only):

- `model` (default): LanguageTool is never imported or initialized —
  behavior is unchanged from before this feature existed.
- `languagetool`: eager init at startup. On failure the backend still
  starts and `fix_wording` returns a normalized `configuration` (grammar
  extra missing) or `unavailable` (init failed, e.g. no Java) error.
  `AUDISOR_FIX_FALLBACK` is ignored.
- `auto` + `fallback=none`: eager init attempt; on failure `fix_wording`
  is unavailable, backend up.
- `auto` + `fallback=model`: eager init attempt; on failure the model
  serves `fix_wording` with `fallback_used=true` and a `fallback_reason`
  in the envelope — the fallback is always visible, never silent.
- Unknown values for either variable → normalized `configuration` error.

Eager initialization means there is no request-time surprise download.

Provenance: when LanguageTool serves the request the envelope reports
`provider = {"id": "languagetool", "source": "local"}`, `usage = null`,
`engine = "languagetool"`; the model path preserves the real provider
identity and token usage with `engine = "model"`.

### Grammar extra (pinned runtime)

LanguageTool support ships as an optional extra:

```powershell
uv sync --all-extras   # installs dev + grammar extras
```

- Pinned package: `language-tool-python==3.4.0` (declared in
  `pyproject.toml` `[project.optional-dependencies] grammar`, locked in
  `uv.lock`).
- LanguageTool distribution fetched by 3.4.0: **6.8**, downloaded from
  `https://languagetool.org/download/` with an upstream-pinned SHA-256
  verified by the package, cached under
  `%USERPROFILE%\.cache\language_tool_python`. After the cache is
  populated, startup works fully offline (verified).
- **Java is required** at runtime (LanguageTool runs a local Java
  server). Without Java on `PATH`, initialization fails and the matrix
  above applies.
- API note: 3.4.0 `Match` objects expose snake_case `error_length` and
  `rule_id` only — the camelCase spellings do not exist (regression
  guarded by tests).

## Authentication interface

`audisor_assistant.auth.ports.AuthProvider` is the integration boundary:
`authenticate(headers) -> AuthContext(subject, environment, workspace_id)`,
raising `AuthenticationError` to reject.

The only shipped adapter is `DevelopmentAuthProvider`:

- honored **only** when `AUDISOR_ASSISTANT_ENV` is `development` (default)
  or `test`; identity comes from the `x-audisor-dev-user` header;
- in `production` it rejects **every** request with 401 — no production
  authentication vendor has been selected, and this adapter never
  masquerades as one.

A production deployment must supply a real `AuthProvider` implementation
behind this interface. No vendor (Supabase, Clerk, Auth0, …) is chosen or
wired by this codebase.

## CORS (opt-in, exact origins only)

By default the backend adds **no** CORS headers at all: same-origin
requests and the Vite dev proxy (`web/vite.config.ts` forwards
`/v1/assistant` to `127.0.0.1:8799`) work exactly as before, and any
cross-origin browser call is refused by the browser.

To allow a browser app served from another origin, set
`AUDISOR_CORS_ORIGINS` to a comma-separated list of **exact** origins:

- entries are trimmed, deduplicated, and lowercased on scheme+host;
- each entry must be exactly `http(s)://host[:port]` — no path, query,
  fragment, or userinfo;
- `*` and wildcard patterns are rejected — startup fails with the
  offending entry named. A malformed allow-list is never partially
  applied;
- only `POST`/`OPTIONS` and the `content-type` /
  `x-audisor-dev-user` headers are allowed; credentials are never
  allowed.

Examples:

```powershell
# Local Vite dev server on another port
$env:AUDISOR_CORS_ORIGINS = 'http://localhost:5173'

# WebContainer preview: copy the EXACT preview origin the host page
# shows (it is unique per boot). Arbitrary *.webcontainer-api.io or
# StackBlitz origins are never pre-allowed.
$env:AUDISOR_CORS_ORIGINS = 'https://abc123--5173.local-corp.webcontainer-api.io'

# Hosted frontend (HTTPS only)
$env:AUDISOR_CORS_ORIGINS = 'https://app.example.com,https://staging.example.com'
```

`run-dev.ps1` prints the effective value (`cors origins : …` or
`(disabled)`) in its configuration banner.

## Running locally (development)

Use `run-dev.ps1` — it prints the effective non-secret configuration,
validates it per provider (refusing to start `local-openai-compatible`
with an empty `AUDISOR_MODEL_ID`), then serves `http://127.0.0.1:8799`:

```powershell
cd openai_project/assistant_backend
.\run-dev.ps1 -Model qwen2.5-coder:7b   # real local model via Ollama
```

The deterministic fake provider is for tests and offline demos only. Its
canned results are identical for every input, so never use it to evaluate
answer quality; the UI provenance badge shows `fake-deterministic` when it
is active:

```powershell
.\run-dev.ps1 -Provider fake-deterministic
```

Example request:

```powershell
curl -X POST http://127.0.0.1:8799/v1/assistant/requests `
  -H "content-type: application/json" `
  -H "x-audisor-dev-user: dev-user" `
  -d '{"request_id":"r1","mode":"fix_wording","text":"i has went home"}'
```

For a real local model, set `AUDISOR_PROVIDER=local-openai-compatible`
and `AUDISOR_MODEL_ID` (plus `AUDISOR_BASE_URL` if not Ollama's default).

## Tests

```powershell
uv run pytest                  # unit + integration (live markers excluded)
uv run pytest -m live_local    # optional smoke; needs a reachable local model
uv run pytest -m live_semantic # optional semantic evidence; needs a real model
```

## Privacy

- Raw user text never appears in backend logs; only sanitized metadata
  (`request_id`, mode, status, provider source, timestamps, token usage).
- Public error messages are stripped of tokens, keys, headers, and
  filesystem paths.
- `visualize_design` diagram code is sanitized (no `click` directives,
  `javascript:` URLs, script tags, or init directives).

## Open production decisions (not made here)

1. Production authentication provider selection and integration.
2. Account/history persistence strategy (currently client-side, typed
   `HistoryStore` interface; backend stores no user content).
3. Billing / usage metering.
4. Cloud model provider selection and credential management.
5. Deployment target and transport hardening (TLS, gateway, rate limits).
