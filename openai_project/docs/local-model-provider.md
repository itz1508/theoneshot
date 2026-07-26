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
