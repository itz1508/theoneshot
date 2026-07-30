# Usage-accounting integration map

Baseline commit: `cf8b0d0140df0b58786e6a5e689316bdc53f5894`
(model selection, native Anthropic provider, kind-aware visualisation).

This map records the **final integrated boundary** between the
usage-accounting foundation and the assistant backend/frontend. The
cloud-anthropic usage conflict flagged in the first version of this map
is resolved through a sanitised native-usage passthrough — no provider
was downgraded to OpenAI-style accounting.

## Provider reply contract (final)

`providers/base.py :: CompletionReply` carries:

| Field | Meaning |
|---|---|
| `usage` | OpenAI-style compatibility values, unchanged for existing consumers |
| `native_usage` | Sanitised provider-native usage object — usage metadata only (whitelisted counters); never the full provider response, prompt, output, headers, credentials, or request payload |
| `native_usage_invalid` | True only when the provider reported usage but its native shape was malformed |
| `model` | Effective model resolved and frozen at the provider boundary (after `model_override` handling) |

The Anthropic adapter whitelists `input_tokens`, `output_tokens`,
`cache_read_input_tokens`, `cache_creation_input_tokens`. Unknown cache
categories are omitted (never coerced to zero) and normalize to `None`.

## Source-selection rule

`usage/normalizer.py :: select_reply_usage` (consumed by
`UsageAccountingService.finalize_from_reply`):

1. `native_usage_invalid` → `unavailable` usage + warning
   `native_usage_invalid` — **no silent fallback**.
2. valid native usage → provider-specific normalizer
   (`normalize_anthropic_usage` for cloud-anthropic), cache categories
   preserved.
3. native absent, compatibility present → `normalize_openai_usage`
   relabelled `source=provider_compatible` + warning
   `compatibility_usage_fallback`.
4. both absent → warning `provider_usage_missing`.

`usage/calculator.py` refuses an exact actual cost when a billable
category's tokens are unknown (`None`): the finalization carries a
warning instead of a fabricated charge.

## Effective model

Every provider (and the DI misconfiguration stub) exposes
`effective_model(model_override)` — the single model-resolution point.
The request body, `CompletionReply.model`,
`PreparedCompletionRequest.model`, pricing resolution, the ledger, and
`UsageAccountingEvidence.model` all consume it; accounting never
recomputes override handling.

## Request-lifecycle wiring

`application/usage_integration.py` (observe-first, never raises into
the request path):

- `UsageAccountingIntegration.from_environment()` — built in
  `api/dependencies.py :: get_service`; `AUDISOR_USAGE_MODE=off` or any
  configuration failure disables accounting (`None`).
- One provider invocation = one attempt (`attempt_id "1"`):
  `begin_attempt` reserves (preflight + ledger `reserved`),
  `finalize_reply` / `finalize_failure` always reach a terminal ledger
  phase — success, malformed usage, and provider failure alike.
- Call sites: `application/service.py :: _run_model_mode` (all model
  modes) and `application/fix_engines.py :: ModelFixEngine.run`
  (fix_wording model engine). `LanguageToolFixEngine` attaches explicit
  `not_applicable` evidence — non-LLM paths never report tokens.

## Public envelope and frontend

- `schemas/responses.py :: AssistantResponse.accounting` —
  `UsageAccountingEvidence | None`; mirrored in
  `openai_project/schemas/assistant/response.schema.json`
  (`$defs/usageAccountingEvidence`, strict, money as decimal strings)
  and `web/src/features/writing-design-assistant/usageAccounting.ts`.
- Estimates (`estimate`/`estimated_cost`) and provider-reported actuals
  (`actual`/`actual_cost`) stay separate fields end to end.
- `UsagePanel` is mounted in `WritingDesignAssistant` and renders when
  the envelope carries accounting evidence.

## Known limitations

1. When the fix-wording model engine raises after finalizing
   (`ProviderError` re-raise, invalid JSON parse), the ledger reaches a
   terminal phase but the raised error path loses the envelope
   evidence for that engine attempt.
2. Enforce-mode admission decisions are recorded in evidence and the
   ledger but do not yet block provider calls (observe-first rollout).
3. Exact actual cost is unavailable when a pricing record bills a cache
   category the provider did not report (ruled: unknown ≠ zero).
