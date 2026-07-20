# Provider-neutral Audisor API

Audisor's local API dispatches work through one explicitly selected provider. Core task, build-preparation, and build-execution services depend only on the typed `WorkerProvider` contract; provider-specific HTTP endpoints, request payloads, authentication, and model identifiers stay inside adapters.

Audisor does not require Fireworks. Audisor does not require a fixed model. Provider switching is configuration-driven. Automatic provider fallback is not enabled. Provider selection policy will later belong to A-Flow.

## Local startup

From `openai_project/runtime`, install the locked development environment and bind only to loopback:

```powershell
uv sync --extra dev --locked
uv run uvicorn audisor.main:app --host 127.0.0.1 --port 8000
```

Use `GET http://127.0.0.1:8000/health` for liveness and `GET http://127.0.0.1:8000/ready` for generic readiness.

## Provider selection

Set `AUDISOR_PROVIDER` to one registered provider ID:

- `fireworks`
- `local-openai-compatible`

There is no default provider and no fallback. A missing, empty, invalid, or unknown selection leaves readiness degraded and causes provider-backed requests to fail with a stable configuration error.

Provider-specific configuration remains namespaced under separate adapter instructions:

```text
Provider adapters
├── Fireworks
└── Local OpenAI-compatible
```

| Provider | Required configuration | Optional configuration |
| --- | --- | --- |
| `fireworks` | `FIREWORKS_API_KEY`, `FIREWORKS_BASE_URL`, `FIREWORKS_MODEL` | none |
| `local-openai-compatible` | `LOCAL_MODEL_BASE_URL`, `LOCAL_MODEL_ID` | `LOCAL_MODEL_API_KEY` |

Do not place credentials in `AUDISOR_PROVIDER`, request bodies, logs, readiness output, or durable build evidence.

## Data root behavior

Set `AUDISOR_DATA_DIR` to a safe external directory when an explicit location is required. Otherwise the runtime selects the platform user-data directory. Product source, snapshot, reference repositories, and protected skill paths cannot be used as the data root. `/ready` reports only a boolean data-root result and does not expose the resolved private path.

## Endpoints

- `GET /health` proves process liveness only. It does not construct, contact, or validate a provider.
- `GET /ready` reports the selected provider ID, generic configuration state, whether adapter capabilities loaded, data-root readiness, and published-schema readiness. It does not contact the provider or reveal model IDs or credentials.
- `POST /v1/tasks` executes a validated batch of typed text tasks.
- `POST /v1/builds/prepare` requests and validates a typed build plan before atomic persistence.
- `POST /v1/builds/{build_id}/executions` requests mutation-only action plans and applies locally enforced structured filesystem mutations in an isolated workspace.
- `POST /v1/operations` accepts a host-agnostic canonical operation request and routes it through `AudisorOperationExecutor`.
- `POST /v1/operations/tasks` accepts a batch of `TaskInput` items, submits each as a canonical `analyze` operation, and returns consolidated results.

Provider-backed endpoints do not execute arbitrary commands, tests, scripts, or shells. Executable validation remains deferred until a separately authorized sandbox service exists.

## Host-agnostic canonical operations

The `/v1/operations` endpoints are the canonical entry point for all hosts (Codex, generic MCP, CLI, Responses-compatible). They translate legacy `OperationRequest` envelopes into `AudisorOperationRequest`, execute through `AudisorOperationExecutor`, and normalize results back into the legacy `OperationResponse` shape. All paths share the same executor instance and enforce authority, mutation policy, idempotency, artifact persistence, and canonical result normalization.

## Capabilities and errors

Each adapter reports `text`, `vision`, `tool_calls`, `structured_output`, and `streaming` capabilities. Core code rejects unsupported work before invoking the adapter and never infers capabilities from a model name.

Public provider errors use stable categories: configuration, unavailable, authentication, rate limited, timeout, invalid response, permanent request, and unsupported capability. Public errors are sanitized; provider payloads, headers, credentials, and raw transport exceptions are not returned.

## Adding another provider

Implement the typed provider contract with a stable lowercase `provider_id`, adapter-owned configuration, declared capabilities, typed `TaskInput` to `TaskOutput` execution, and normalized failures. Register its lazy factory in the composition layer. The provider-neutral services and endpoint schemas do not need provider-specific branches.

Use deterministic fake-provider and shared conformance tests before any optional live smoke. Live tests run only when every required provider variable is configured, must make no fallback request, and must persist only sanitized proof metadata.

## Current limitations

The public task input is text-only. Provider switching is not automatic. The execution endpoint supports isolated, locally enforced structured filesystem mutations only; it does not run Python, tests, scripts, shells, or arbitrary validation commands. Executable validation, A-Flow policy, frontend, tool integration, deployment, and additional providers are outside this foundation phase.
