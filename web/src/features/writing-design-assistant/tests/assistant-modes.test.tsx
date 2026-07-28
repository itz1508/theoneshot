/**
 * assistant-modes.test.tsx — mode rendering and request behavior:
 * all modes render, selecting a mode changes the outgoing request,
 * loading state appears, and translate-without-selection shows the
 * structured selection-required state.
 */

import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { WritingDesignAssistant } from '../WritingDesignAssistant'
import { MODES } from '../modes'
import type { AssistantResponse } from '../types'

vi.mock('mermaid', () => ({
  default: {
    initialize: vi.fn(),
    render: vi.fn().mockResolvedValue({ svg: '<svg></svg>' }),
  },
}))

function envelope(overrides: Partial<AssistantResponse> = {}): AssistantResponse {
  return {
    request_id: 'r1',
    mode: 'fix_wording',
    status: 'completed',
    result: { corrected_text: 'Fixed.' },
    warnings: [],
    provider: { id: 'fake-deterministic', source: 'local' },
    usage: null,
    uncertainty: [],
    ...overrides,
  }
}

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'content-type': 'application/json' },
  })
}

function setup(fetchImpl: typeof fetch) {
  return render(<WritingDesignAssistant submitOptions={{ fetchImpl }} />)
}

describe('mode rendering', () => {
  it('renders all assistant modes', () => {
    setup(vi.fn())
    expect(MODES).toHaveLength(6)
    for (const mode of MODES) {
      expect(screen.getByRole('button', { name: new RegExp(mode.label, 'i') })).toBeInTheDocument()
    }
  })

  it('marks the selected mode as pressed', () => {
    setup(vi.fn())
    const fixButton = screen.getByRole('button', { name: /improve my message/i })
    expect(fixButton).toHaveAttribute('aria-pressed', 'true')
    const teachButton = screen.getByRole('button', { name: /teach clearly/i })
    fireEvent.click(teachButton)
    expect(teachButton).toHaveAttribute('aria-pressed', 'true')
    expect(fixButton).toHaveAttribute('aria-pressed', 'false')
  })
})

describe('request behavior', () => {
  it('sends the selected mode in the request body', async () => {
    const fetchImpl = vi
      .fn()
      .mockResolvedValue(jsonResponse(envelope({ mode: 'expand_idea', result: {
        expanded_text: 'More.',
        preserved_intent: 'Intent.',
        added_assumptions: [],
        uncertainty: [],
      } })))
    setup(fetchImpl)
    fireEvent.click(screen.getByRole('button', { name: /expand idea/i }))
    fireEvent.change(screen.getByLabelText(/your text/i), { target: { value: 'rough idea' } })
    fireEvent.click(screen.getByRole('button', { name: /^send$/i }))
    await waitFor(() => expect(fetchImpl).toHaveBeenCalledTimes(1))
    const body = JSON.parse((fetchImpl.mock.calls[0][1] as RequestInit).body as string)
    expect(body.mode).toBe('expand_idea')
    expect(body.text).toBe('rough idea')
  })

  it('sends clarify intent and preserves the editable original input', async () => {
    const fetchImpl = vi.fn().mockResolvedValue(
      jsonResponse(envelope({
        mode: 'fix_wording',
        result: {
          corrected_text: 'A clearer message.',
          changes: [],
          no_changes_needed: false,
          inferred_intent: 'Explain the request.',
          tone: 'neutral',
          context: 'Development discussion.',
          assumptions: [],
          uncertainty: [],
        },
      })),
    )
    setup(fetchImpl)
    fireEvent.click(screen.getByRole('button', { name: /improve my message/i }))
    fireEvent.change(screen.getByLabelText(/your text/i), { target: { value: 'rough thought' } })
    fireEvent.click(screen.getByRole('button', { name: /^send$/i }))
    expect(await screen.findByDisplayValue('A clearer message.')).toBeInTheDocument()
    expect(screen.getByLabelText(/your text/i)).toHaveValue('rough thought')

    fireEvent.change(screen.getByLabelText(/^improved message$/i), { target: { value: 'edited message' } })
    fireEvent.click(screen.getByRole('button', { name: /refine this message/i }))
    await waitFor(() => expect(fetchImpl).toHaveBeenCalledTimes(2))
    const refinedBody = JSON.parse((fetchImpl.mock.calls[1][1] as RequestInit).body as string)
    expect(refinedBody.mode).toBe('fix_wording')
    expect(refinedBody.text).toBe('edited message')
  })

  it('shows the loading state while the request is in flight', async () => {
    let resolveFetch: (value: Response) => void = () => {}
    const fetchImpl = vi.fn().mockReturnValue(
      new Promise<Response>((resolve) => {
        resolveFetch = resolve
      }),
    )
    setup(fetchImpl)
    fireEvent.change(screen.getByLabelText(/your text/i), { target: { value: 'hello' } })
    fireEvent.click(screen.getByRole('button', { name: /^send$/i }))
    expect(await screen.findByText(/working on it/i)).toBeInTheDocument()
    resolveFetch(jsonResponse(envelope()))
    await waitFor(() => expect(screen.queryByText(/working on it/i)).not.toBeInTheDocument())
  })

  it('shows selection-required for translate mode without a selected term', async () => {
    const fetchImpl = vi.fn().mockResolvedValue(
      jsonResponse(
        envelope({
          mode: 'translate_slang_jargon',
          status: 'uncertainty',
          result: {
            selection_required: true,
            message: 'Select the slang or jargon term to translate.',
          },
        }),
      ),
    )
    setup(fetchImpl)
    fireEvent.click(screen.getByRole('button', { name: /translate slang/i }))
    fireEvent.change(screen.getByLabelText(/your text/i), { target: { value: 'circle back' } })
    fireEvent.click(screen.getByRole('button', { name: /^send$/i }))
    expect(
      await screen.findByText(/select the slang or jargon term/i),
    ).toBeInTheDocument()
  })
})
