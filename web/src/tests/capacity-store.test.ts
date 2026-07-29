/**
 * Store-level tests for the composer capacity meter plumbing.
 *
 * 2.  Editing the draft updates the estimate (debounced, no per-keystroke call)
 * 3.  Clearing the draft reduces the estimate
 * 4.  Existing conversation history contributes to the estimate
 * 6.  Changing models updates the maximum allowance
 * 8.  A stale debounced estimate cannot overwrite a newer estimate
 * 12. Backend unavailable does not activate demo data
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { useAppStore } from '../store/taskStore'
import type { ChatEstimateResponse, ChatEstimatePayload } from '../agent/chatCapacity'
import type { ChatMessage } from '../agent/types'

const RESERVED = 2048
const DEFAULT_CONTEXT = 8192
const MODEL_CONTEXTS: Record<string, number> = {
  'model-small': 8192,
  'model-large': 32768,
}

/** Deterministic estimate mirroring the backend's character-ratio method. */
function estimateFor(body: ChatEstimatePayload): ChatEstimateResponse {
  const historyChars = body.history.reduce((n, t) => n + t.content.length, 0)
  const context = MODEL_CONTEXTS[body.model ?? ''] ?? DEFAULT_CONTEXT
  return {
    // +50 stands in for the system prompt the backend always includes
    estimated_input_tokens: Math.ceil((body.message.length + historyChars) / 4) + 50,
    reserved_output_tokens: RESERVED,
    context_limit: context,
    usable_input_tokens: context - RESERVED,
    model: body.model ?? 'default-model',
    method: 'character_ratio',
    confidence: 'approximate',
  }
}

function mockEstimateFetch() {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (_url, init) => {
    const body = JSON.parse(String(init?.body)) as ChatEstimatePayload
    return {
      ok: true,
      status: 200,
      json: async () => estimateFor(body),
    } as Response
  })
}

function lastRequestBody(
  spy: ReturnType<typeof mockEstimateFetch>,
): ChatEstimatePayload {
  const call = spy.mock.calls[spy.mock.calls.length - 1]
  expect(call).toBeDefined()
  return JSON.parse(String((call[1] as RequestInit).body)) as ChatEstimatePayload
}

/** Flush the fetch → json → set() microtask chain (fake-timer safe). */
async function flushAsync() {
  for (let i = 0; i < 6; i += 1) await Promise.resolve()
}

beforeEach(() => {
  useAppStore.getState().reset()
})

afterEach(() => {
  vi.restoreAllMocks()
  vi.useRealTimers()
})

// ─── 2 + 3. Draft edits update the estimate through the debounce ───

describe('Draft-driven estimation', () => {
  it('debounces typing into a single request that updates the estimate', async () => {
    vi.useFakeTimers()
    const fetchSpy = mockEstimateFetch()
    const store = useAppStore.getState()

    store.setDraft('W')
    store.setDraft('Wh')
    store.setDraft('What is the weather today?')
    // No completion-provider or estimate call per keystroke
    expect(fetchSpy).not.toHaveBeenCalled()

    await vi.advanceTimersByTimeAsync(300)
    await flushAsync()

    expect(fetchSpy).toHaveBeenCalledTimes(1)
    expect(lastRequestBody(fetchSpy).message).toBe('What is the weather today?')
    const { capacity } = useAppStore.getState()
    expect(capacity.status).toBe('ready')
    expect(capacity.estimatedInput).toBe(
      estimateFor({ message: 'What is the weather today?', history: [] })
        .estimated_input_tokens,
    )
  })

  it('clearing the draft reduces the estimate', async () => {
    vi.useFakeTimers()
    mockEstimateFetch()
    const store = useAppStore.getState()

    store.setDraft('x'.repeat(400))
    await vi.advanceTimersByTimeAsync(300)
    await flushAsync()
    const filled = useAppStore.getState().capacity.estimatedInput
    expect(filled).not.toBeNull()

    store.setDraft('')
    await vi.advanceTimersByTimeAsync(300)
    await flushAsync()
    const cleared = useAppStore.getState().capacity.estimatedInput
    expect(cleared).not.toBeNull()
    expect(cleared!).toBeLessThan(filled!)
  })
})

// ─── 4. Conversation history contributes to the estimate ───

describe('History contribution', () => {
  it('sends existing conversation turns with the estimate request', async () => {
    const fetchSpy = mockEstimateFetch()
    const store = useAppStore.getState()

    store.requestEstimateNow()
    await flushAsync()
    const withoutHistory = useAppStore.getState().capacity.estimatedInput

    const messages: ChatMessage[] = [
      { id: 'm1', role: 'user', content: 'Earlier question '.repeat(20) },
      { id: 'm2', role: 'agent', content: 'Earlier answer '.repeat(20) },
    ]
    useAppStore.setState({ messages })
    store.requestEstimateNow()
    await flushAsync()

    const body = lastRequestBody(fetchSpy)
    expect(body.history).toEqual([
      { role: 'user', content: messages[0].content },
      { role: 'assistant', content: messages[1].content },
    ])
    const withHistory = useAppStore.getState().capacity.estimatedInput
    expect(withHistory!).toBeGreaterThan(withoutHistory!)
  })
})

// ─── 6. Changing models updates the maximum allowance ───

describe('Model selection', () => {
  it('refreshes the allowance immediately for the selected model', async () => {
    const fetchSpy = mockEstimateFetch()
    const store = useAppStore.getState()

    store.setSelectedModel('model-large')
    await flushAsync()
    expect(lastRequestBody(fetchSpy).model).toBe('model-large')
    expect(useAppStore.getState().capacity.usableInput).toBe(32768 - RESERVED)

    store.setSelectedModel('model-small')
    await flushAsync()
    expect(lastRequestBody(fetchSpy).model).toBe('model-small')
    expect(useAppStore.getState().capacity.usableInput).toBe(8192 - RESERVED)
  })
})

// ─── 8. A stale estimate cannot overwrite a newer one ───

describe('Stale estimate protection', () => {
  it('ignores an older response that resolves after a newer one', async () => {
    const resolvers: Array<(response: Response) => void> = []
    vi.spyOn(globalThis, 'fetch').mockImplementation(
      () => new Promise<Response>((resolve) => resolvers.push(resolve)),
    )
    const store = useAppStore.getState()

    store.requestEstimateNow() // request A (older)
    store.requestEstimateNow() // request B (newer)
    expect(resolvers).toHaveLength(2)

    const respond = (estimated: number): Response =>
      ({
        ok: true,
        status: 200,
        json: async () =>
          ({
            ...estimateFor({ message: '', history: [] }),
            estimated_input_tokens: estimated,
          }) satisfies ChatEstimateResponse,
      }) as Response

    // Newer request resolves first
    resolvers[1](respond(200))
    await flushAsync()
    expect(useAppStore.getState().capacity.estimatedInput).toBe(200)

    // Older (stale) response arrives late — it must not win
    resolvers[0](respond(100))
    await flushAsync()
    expect(useAppStore.getState().capacity.estimatedInput).toBe(200)
  })
})

// ─── 12. Backend unavailable does not activate demo data ───

describe('Backend unavailable', () => {
  it('reports an explicit unavailable state with no fabricated values', async () => {
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(
      new TypeError('Failed to fetch'),
    )
    const store = useAppStore.getState()

    store.requestEstimateNow()
    await flushAsync()

    const { capacity, messages } = useAppStore.getState()
    expect(capacity.status).toBe('unavailable')
    expect(capacity.estimatedInput).toBeNull()
    expect(capacity.contextLimit).toBeNull()
    expect(capacity.usableInput).toBeNull()
    expect(capacity.overLimit).toBe(false)
    // No demo conversation or placeholder data appears anywhere
    expect(messages).toEqual([])
  })
})
