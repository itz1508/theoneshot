# Local model provider

Audisor supports any local server that exposes the expected OpenAI-compatible
request and response shape. Ollama with `qwen2.5-coder:7b` is the built-in
default, not a required model.

## Configuration

Set these environment variables before starting the runtime:

```powershell
$env:AUDISOR_PROVIDER = "local-openai-compatible"
$env:AUDISOR_BASE_URL = "http://127.0.0.1:11434"
$env:AUDISOR_MODEL_ID = "qwen2.5-coder:7b"
$env:AUDISOR_TIMEOUT_SECONDS = "300"
$env:AUDISOR_MAX_TOKENS = "160"
```

To test another compatible local model, change only `AUDISOR_BASE_URL` and
`AUDISOR_MODEL_ID`:

```powershell
$env:AUDISOR_BASE_URL = "http://127.0.0.1:1234"
$env:AUDISOR_MODEL_ID = "another-local-model"
```

The server must accept the runtime's chat request, return a usable text
response, and remain reachable for the configured timeout. Provider errors,
timeouts, rate limits, authentication failures, and invalid responses are
reported through the normalized provider error boundary.

## Model listing and per-request override

`GET /v1/assistant/models` (authenticated) reports the active provider, its
configured default model, and the models selectable within that provider.
For the local provider the list comes from the engine's `/api/tags` probe;
when the engine is unreachable the endpoint degrades to the configured model
with `reachable: false` instead of failing. Cloud providers return a fixed,
server-configured list (`AUDISOR_ASSISTANT_CLOUD_MODEL_CHOICES`, comma
separated) and are never probed, so `reachable` is `null`.

A request may set the optional `model` field to pick one of those models for
that request only. The override selects a model *within* the configured
provider; provider selection itself is never client-controlled.

`GET /v1/assistant/health` is an unauthenticated liveness probe returning
`{"status": "ok"}` plus the provider identity.

## Cloud providers

Two cloud provider ids exist: `cloud-openai-compatible` (OpenAI-style chat
completions) and `cloud-anthropic` (native Anthropic Messages API,
`x-api-key` header, default base URL `https://api.anthropic.com`). Both read:

```powershell
$env:AUDISOR_PROVIDER = "cloud-anthropic"
$env:AUDISOR_ASSISTANT_CLOUD_BASE_URL = "https://api.anthropic.com"   # optional
$env:AUDISOR_ASSISTANT_CLOUD_MODEL_ID = "claude-sonnet-4-6"
$env:AUDISOR_ASSISTANT_CLOUD_API_KEY = "<secret, never committed>"
$env:AUDISOR_ASSISTANT_CLOUD_MODEL_CHOICES = "claude-sonnet-4-6,claude-haiku-4-5-20251001"  # optional
```

Startup fails fast (`RuntimeError`) when a cloud provider is selected and
`AUDISOR_ASSISTANT_CLOUD_API_KEY` is unset — a cloud deployment must never
come up half-configured. `run-dev.ps1` enforces the same rule before launch.

## Reliability boundary

Local models are a best-effort development and offline path. Hardware,
quantization, context limits, model readiness, and local server load can
change latency and output quality. A local result is not a production
performance guarantee.

For production semantic review, prefer the cloud provider path when available.
If local semantic review times out or becomes unavailable, preserve the
operation and return an unresolved or decision-required result; do not treat a
timeout as successful review.

## Validation

Before using a new local model, run the runtime suite and a small provider
smoke against that model. Confirm the selected provider, base URL, model ID,
timeout, response shape, and error handling. Do not place credentials in the
repository or commit local model files.

This document covers Audisor's adapter configuration only. Ollama, Qwen, and
other model servers remain third-party software with their own licenses and
operational requirements.
