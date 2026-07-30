import type { AflowIssue, AflowIssuePage, AflowStatus } from './types'
import type { ClaimRequest, ClaimResponse } from '../../agent/types'

const DEV_HEADERS = { 'x-audisor-dev-user': 'operator' }

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { ...DEV_HEADERS, ...(init?.headers ?? {}) },
  })
  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: response.statusText }))
    throw new Error(JSON.stringify(body.detail ?? body))
  }
  return response.json() as Promise<T>
}

export const getAflowStatus = () => request<AflowStatus>('/v1/aflow/status')
export const probeAflowProviders = () =>
  request<AflowStatus>('/v1/aflow/provider-probe', { method: 'POST' })
export const getAflowIssues = () => request<AflowIssuePage>('/v1/aflow/issues?limit=100')
export const getAflowIssue = (issueId: string) =>
  request<AflowIssue>(`/v1/aflow/issues/${encodeURIComponent(issueId)}`)

export async function retryAflowIssue(
  issue: AflowIssue,
  provider: 'local' | 'fallback',
): Promise<Record<string, unknown>> {
  if (!issue.operation_id) throw new Error(issue.retry.instruction)
  const status = await request<{ detail: { history_length: number } }>(
    `/v1/operations/${encodeURIComponent(issue.operation_id)}/status`,
  )
  const idempotencyKey =
    typeof crypto !== 'undefined' && 'randomUUID' in crypto
      ? crypto.randomUUID()
      : `retry-${Date.now()}-${Math.random().toString(36).slice(2)}`
  const action = provider === 'local' ? 'retry-local' : 'continue-with-fallback'
  return request(`/v1/aflow/issues/${encodeURIComponent(issue.issue_id)}/${action}`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({
      operation_version: status.detail.history_length,
      idempotency_key: idempotencyKey,
    }),
  })
}

/**
 * Recursively sort all object keys for canonical JSON (matches Python sort_keys=True).
 */
function sortKeys(value: unknown): unknown {
  if (value === null || typeof value !== 'object') return value
  if (Array.isArray(value)) return value.map(sortKeys)
  const sorted: Record<string, unknown> = {}
  for (const key of Object.keys(value as Record<string, unknown>).sort()) {
    sorted[key] = sortKeys((value as Record<string, unknown>)[key])
  }
  return sorted
}

/**
 * Canonical JSON serializer matching Python's json.dumps(sort_keys=True, separators=(",", ":")).
 * Recursively sorts object keys and uses compact separators with no spaces.
 */
function canonicalJSON(value: unknown): string {
  if (value === null) return 'null'
  if (typeof value === 'boolean') return value ? 'true' : 'false'
  if (typeof value === 'number') return JSON.stringify(value)
  if (typeof value === 'string') return JSON.stringify(value)
  if (Array.isArray(value)) {
    return '[' + value.map((v) => canonicalJSON(v)).join(',') + ']'
  }
  if (typeof value === 'object') {
    const entries = Object.entries(value as Record<string, unknown>)
    return (
      '{' +
      entries
        .map(([k, v]) => JSON.stringify(k) + ':' + canonicalJSON(v))
        .join(',') +
      '}'
    )
  }
  return 'null'
}

/**
 * Compute SHA-256 digest matching the backend's compute_argument_digest.
 * Canonical form: JSON with recursively sorted keys and compact separators
 * (no spaces), matching Python's json.dumps(sort_keys=True, separators=(",", ":")).
 */
export async function computeArgumentDigest(
  callId: string,
  toolName: string,
  args: Record<string, unknown>,
): Promise<string> {
  const canonical = canonicalJSON(
    sortKeys({ call_id: callId, tool_name: toolName, arguments: args }),
  )
  const encoded = new TextEncoder().encode(canonical)
  const hash = await crypto.subtle.digest('SHA-256', encoded)
  return Array.from(new Uint8Array(hash))
    .map((b) => b.toString(16).padStart(2, '0'))
    .join('')
}

/**
 * Atomically claim a pending tool execution for a specific claimant (tab).
 * First valid claim wins; subsequent claims from the same claimant are idempotent;
 * claims from different claimants are rejected.
 */
export function claimToolExecution(
  operationId: string,
  payload: ClaimRequest,
): Promise<ClaimResponse> {
  return request<ClaimResponse>(
    `/v1/operations/${encodeURIComponent(operationId)}/claim`,
    {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(payload),
    },
  )
}
