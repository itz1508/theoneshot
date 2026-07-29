/**
 * Chat capacity — live composer token meter support.
 *
 * Talks to POST /v1/chat/estimate (never the completion provider).
 * The meter is a pre-send estimate of the complete next request input
 * and is entirely separate from the provider-reported IN/OUT usage
 * shown as evidence on completed turns.
 */

export interface ChatHistoryEntry {
  role: 'user' | 'assistant'
  content: string
}

/** Wire shape of POST /v1/chat/estimate. */
export interface ChatEstimateResponse {
  estimated_input_tokens: number
  reserved_output_tokens: number
  context_limit: number | null
  usable_input_tokens: number | null
  model: string
  method: string
  confidence: string
}

export type CapacityStatus = 'idle' | 'estimating' | 'ready' | 'unavailable'

/** Store-side meter state derived from the latest applied estimate. */
export interface ChatCapacity {
  status: CapacityStatus
  estimatedInput: number | null
  contextLimit: number | null
  usableInput: number | null
  reservedOutput: number | null
  nearLimit: boolean
  overLimit: boolean
}

/** Warn once the estimate reaches this share of the usable allowance. */
export const NEAR_LIMIT_RATIO = 0.9

export const initialCapacity: ChatCapacity = {
  status: 'idle',
  estimatedInput: null,
  contextLimit: null,
  usableInput: null,
  reservedOutput: null,
  nearLimit: false,
  overLimit: false,
}

export function capacityFromEstimate(estimate: ChatEstimateResponse): ChatCapacity {
  const usable = estimate.usable_input_tokens
  const overLimit = usable != null && estimate.estimated_input_tokens > usable
  const nearLimit =
    usable != null && !overLimit && estimate.estimated_input_tokens >= usable * NEAR_LIMIT_RATIO
  return {
    status: 'ready',
    estimatedInput: estimate.estimated_input_tokens,
    contextLimit: estimate.context_limit,
    usableInput: usable,
    reservedOutput: estimate.reserved_output_tokens,
    nearLimit,
    overLimit,
  }
}

export interface ChatEstimatePayload {
  message: string
  model?: string
  history: ChatHistoryEntry[]
}

export async function fetchChatEstimate(
  payload: ChatEstimatePayload,
  signal: AbortSignal,
): Promise<ChatEstimateResponse> {
  const response = await fetch('/v1/chat/estimate', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Audisor-Dev-User': 'operator',
    },
    body: JSON.stringify(payload),
    signal,
  })
  if (!response.ok) {
    throw new Error(`Estimate request failed: ${response.status}`)
  }
  return response.json() as Promise<ChatEstimateResponse>
}
