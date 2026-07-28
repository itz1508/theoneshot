/**
 * assistantApi.ts — the only network surface of the feature.
 *
 * Talks exclusively to the assistant backend's relative endpoint.
 * No model-provider URL, key, or provider selection ever appears here:
 * provider choice is a server-side concern.
 */

import type { AssistantMode, AssistantRequest, AssistantResponse } from '../types'

/** Relative backend endpoint; in dev, Vite proxies it to the local backend. */
export const ASSISTANT_ENDPOINT = '/v1/assistant/requests'

/** Development identity header consumed by the backend's dev auth adapter. */
export const DEV_IDENTITY_HEADER = 'x-audisor-dev-user'

/**
 * Resolves the endpoint from VITE_ASSISTANT_API_URL.
 *
 * Unset or empty → the relative endpoint (same-origin / Vite proxy path,
 * unchanged behavior). Set → must be an absolute http(s) URL with no query
 * or fragment; the endpoint path is appended to it. A malformed value is a
 * configuration error — never a silent fallback and never fake output.
 */
export function resolveAssistantEndpoint(
  raw: string | undefined = import.meta.env.VITE_ASSISTANT_API_URL,
): { url: string } | { error: 'configuration' } {
  const value = raw?.trim()
  if (!value) return { url: ASSISTANT_ENDPOINT }
  let parsed: URL
  try {
    parsed = new URL(value)
  } catch {
    return { error: 'configuration' }
  }
  if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
    return { error: 'configuration' }
  }
  if (parsed.search !== '' || parsed.hash !== '') {
    return { error: 'configuration' }
  }
  const base = (parsed.origin + parsed.pathname).replace(/\/+$/, '')
  return { url: base + ASSISTANT_ENDPOINT }
}

export interface SubmitOptions {
  /** Injected for tests; defaults to the global fetch. */
  fetchImpl?: typeof fetch
  /** Development identity; production auth is a pending backend decision. */
  devIdentity?: string
  /** Raw base-URL override for tests; defaults to VITE_ASSISTANT_API_URL. */
  apiBaseUrl?: string
  signal?: AbortSignal
}

export interface SubmitInput {
  mode: AssistantMode
  text: string
  selectedText?: string
  context?: string
  tone?: string
  workspaceId?: string
}

export function newRequestId(): string {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) {
    return crypto.randomUUID()
  }
  return `req-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`
}

export function buildRequest(input: SubmitInput): AssistantRequest {
  const request: AssistantRequest = {
    request_id: newRequestId(),
    mode: input.mode,
    text: input.text,
  }
  if (input.selectedText) request.selected_text = input.selectedText
  if (input.context) request.context = input.context
  if (input.tone) request.tone = input.tone
  if (input.workspaceId) request.workspace_id = input.workspaceId
  return request
}

/** Local failure envelope so the UI always renders one normalized shape. */
function localFailure(
  request: AssistantRequest,
  category: 'unavailable' | 'timeout' | 'invalid_response' | 'configuration',
  message: string,
): AssistantResponse {
  return {
    request_id: request.request_id,
    mode: request.mode,
    status: 'failed',
    result: { error: { category, message } },
    warnings: [],
    provider: null,
    usage: null,
    uncertainty: [],
  }
}

export async function submitAssistantRequest(
  input: SubmitInput,
  options: SubmitOptions = {},
): Promise<AssistantResponse> {
  const fetchImpl = options.fetchImpl ?? fetch
  const request = buildRequest(input)
  const endpoint = resolveAssistantEndpoint(options.apiBaseUrl)
  if ('error' in endpoint) {
    return localFailure(
      request,
      'configuration',
      'VITE_ASSISTANT_API_URL is not a valid absolute http(s) URL.',
    )
  }
  let httpResponse: Response
  try {
    httpResponse = await fetchImpl(endpoint.url, {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        [DEV_IDENTITY_HEADER]: options.devIdentity ?? 'web-dev',
      },
      body: JSON.stringify(request),
      signal: options.signal,
    })
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error
    return localFailure(request, 'unavailable', 'The assistant backend is not reachable.')
  }

  if (httpResponse.status === 401) {
    return {
      ...localFailure(request, 'unavailable', ''),
      result: {
        error: {
          category: 'authentication',
          message: 'The assistant backend rejected the request as unauthenticated.',
        },
      },
    }
  }
  if (httpResponse.status === 422) {
    return {
      ...localFailure(request, 'invalid_response', ''),
      result: {
        error: {
          category: 'invalid_response',
          message: 'The request was rejected by backend validation.',
        },
      },
    }
  }

  // The backend contract never emits HTTP 5xx (failures arrive as normalized
  // envelopes), so a 5xx can only come from a dead proxy or dead upstream.
  if ([500, 502, 503, 504].includes(httpResponse.status)) {
    return localFailure(request, 'unavailable', 'The assistant backend is not reachable.')
  }

  let body: unknown
  try {
    body = await httpResponse.json()
  } catch {
    return localFailure(request, 'invalid_response', 'The backend returned a malformed response.')
  }
  if (!isAssistantResponse(body)) {
    return localFailure(request, 'invalid_response', 'The backend response did not match the contract.')
  }
  return body
}

function isAssistantResponse(value: unknown): value is AssistantResponse {
  if (typeof value !== 'object' || value === null) return false
  const candidate = value as Record<string, unknown>
  return (
    typeof candidate.request_id === 'string' &&
    typeof candidate.mode === 'string' &&
    (candidate.status === 'completed' ||
      candidate.status === 'uncertainty' ||
      candidate.status === 'failed') &&
    typeof candidate.result === 'object' &&
    candidate.result !== null &&
    Array.isArray(candidate.warnings) &&
    Array.isArray(candidate.uncertainty)
  )
}
