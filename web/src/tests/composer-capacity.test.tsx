/**
 * Component-level tests for the composer capacity meter.
 *
 * 1.  Composer displays `estimated / maximum`
 * 7.  Unknown context limit renders an explicit unavailable state
 * 9.  Near-limit state displays a warning
 * 10. Over-limit state prevents submission
 * 11. Completed provider-reported IN/OUT stays distinguishable from the
 *     live composer capacity meter
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { MessageComposer } from '../components/MessageComposer'
import { TokenBadge } from '../components/TokenBadge'
import { useAppStore } from '../store/taskStore'
import type { ChatEstimateResponse } from '../agent/chatCapacity'

const baseEstimate: ChatEstimateResponse = {
  estimated_input_tokens: 23,
  reserved_output_tokens: 2048,
  context_limit: 32768,
  usable_input_tokens: 30720,
  model: 'test-model',
  method: 'character_ratio',
  confidence: 'approximate',
}

function mockEstimate(overrides: Partial<ChatEstimateResponse> = {}) {
  const estimate = { ...baseEstimate, ...overrides }
  return vi.spyOn(globalThis, 'fetch').mockResolvedValue({
    ok: true,
    status: 200,
    json: async () => estimate,
  } as Response)
}

function renderComposer(onSend = vi.fn()) {
  render(
    <MessageComposer
      onSend={onSend}
      anchorMode="user"
      onAnchorModeChange={() => {}}
    />,
  )
  return onSend
}

beforeEach(() => {
  useAppStore.getState().reset()
})

afterEach(() => {
  vi.restoreAllMocks()
})

// ─── 1. Composer displays `estimated / maximum` ───

describe('CapacityMeter display', () => {
  it('shows the estimated input over the usable allowance', async () => {
    mockEstimate()
    renderComposer()

    const meter = await screen.findByRole('status', { name: 'Token capacity' })
    await waitFor(() => expect(meter).toHaveTextContent('23 / 30,720'))
  })

  // ─── 7. Unknown context limit → explicit unavailable state ───

  it('renders "estimated / unknown" when the context limit is unavailable', async () => {
    mockEstimate({ context_limit: null, usable_input_tokens: null })
    renderComposer()

    const meter = await screen.findByRole('status', { name: 'Token capacity' })
    await waitFor(() => expect(meter).toHaveTextContent('23 / unknown'))
  })

  it('renders an explicit unavailable state when the backend cannot be reached', async () => {
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('backend down'))
    renderComposer()

    const meter = await screen.findByRole('status', { name: 'Token capacity' })
    await waitFor(() => expect(meter).toHaveTextContent('capacity unavailable'))
    // No fabricated numbers appear in the meter
    expect(meter).not.toHaveTextContent('/')
  })
})

// ─── 9. Near-limit state displays a warning ───

describe('Near-limit warning', () => {
  it('shows a visible warning when the estimate approaches the usable allowance', async () => {
    // usable 6144, estimated 5900 → ≥ 90% but not over
    mockEstimate({
      estimated_input_tokens: 5900,
      context_limit: 8192,
      usable_input_tokens: 6144,
    })
    renderComposer()

    const warning = await screen.findByRole('alert')
    expect(warning).toHaveTextContent("Approaching the model's context limit")
    expect(warning).toHaveTextContent('5,900 of 6,144')
  })
})

// ─── 10. Over-limit state prevents submission ───

describe('Over-limit submission block', () => {
  it('disables sending and explains why when the estimate exceeds the allowance', async () => {
    mockEstimate({
      estimated_input_tokens: 7000,
      context_limit: 8192,
      usable_input_tokens: 6144,
    })
    useAppStore.setState({ draft: 'a long over-limit draft' })
    const onSend = renderComposer()

    const blocked = await screen.findByRole('alert')
    expect(blocked).toHaveTextContent('Sending is blocked')
    expect(blocked).toHaveTextContent('7,000')
    expect(blocked).toHaveTextContent('6,144')

    const sendBtn = screen.getByTitle(
      'Estimated input exceeds the usable context allowance',
    )
    expect(sendBtn).toBeDisabled()

    // Enter must not submit either
    fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Enter' })
    expect(onSend).not.toHaveBeenCalled()
  })
})

// ─── 11. Completed IN/OUT usage stays distinct from the capacity meter ───

describe('Meter vs completed-turn usage evidence', () => {
  it('keeps the provider-reported usage badge separate from the capacity meter', async () => {
    mockEstimate()
    renderComposer()
    render(
      <TokenBadge
        tokens={{
          input_tokens: 26,
          output_tokens: 9,
          total_tokens: 35,
          cost: null,
          provider: 'local',
        }}
      />,
    )

    const meter = await screen.findByLabelText('Token capacity')
    const badge = screen.getByLabelText('Token usage')

    expect(meter).not.toBe(badge)
    await waitFor(() => expect(meter).toHaveTextContent('23 / 30,720'))
    // The badge reports post-response usage, never the allowance
    expect(badge).toHaveTextContent('in 26')
    expect(badge).toHaveTextContent('out 9')
    expect(badge).not.toHaveTextContent('/')
    expect(badge).not.toHaveTextContent('30,720')
  })
})
