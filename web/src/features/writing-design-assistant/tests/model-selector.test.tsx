/**
 * model-selector.test.tsx — model dropdown behavior:
 * hidden without a listing, default marking, default maps back to '',
 * unreachable notice, and feature-root wiring (listing fetched on
 * mount, chosen model sent in the request, default omits the field).
 */

import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { ModelSelector } from '../components/ModelSelector'
import { WritingDesignAssistant } from '../WritingDesignAssistant'
import type { AssistantModelsResponse, AssistantResponse } from '../types'

function listing(overrides: Partial<AssistantModelsResponse> = {}): AssistantModelsResponse {
  return {
    provider: { id: 'local-openai-compatible', source: 'local' },
    current_model: 'llama3.2:3b',
    available_models: ['llama3.2:3b', 'qwen2.5:7b'],
    reachable: true,
    ...overrides,
  }
}

function envelope(overrides: Partial<AssistantResponse> = {}): AssistantResponse {
  return {
    request_id: 'r1',
    mode: 'fix_wording',
    status: 'completed',
    result: { corrected_text: 'Fixed.' },
    warnings: [],
    provider: { id: 'local-openai-compatible', source: 'local' },
    usage: null,
    uncertainty: [],
    ...overrides,
  }
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}

describe('ModelSelector component', () => {
  it('renders nothing without a listing or with an empty listing', () => {
    const { container, rerender } = render(
      <ModelSelector models={null} value="" onChange={() => {}} />,
    )
    expect(container).toBeEmptyDOMElement()
    rerender(
      <ModelSelector models={listing({ available_models: [] })} value="" onChange={() => {}} />,
    )
    expect(container).toBeEmptyDOMElement()
  })

  it('lists models with the configured default marked', () => {
    render(<ModelSelector models={listing()} value="" onChange={() => {}} />)
    const select = screen.getByLabelText(/model \(local-openai-compatible\)/i)
    expect(select).toHaveValue('llama3.2:3b')
    expect(screen.getByRole('option', { name: 'llama3.2:3b (default)' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'qwen2.5:7b' })).toBeInTheDocument()
  })

  it('reports a non-default choice and maps the default back to the empty override', () => {
    const onChange = vi.fn()
    const { rerender } = render(
      <ModelSelector models={listing()} value="" onChange={onChange} />,
    )
    fireEvent.change(screen.getByLabelText(/model \(/i), { target: { value: 'qwen2.5:7b' } })
    expect(onChange).toHaveBeenLastCalledWith('qwen2.5:7b')
    rerender(<ModelSelector models={listing()} value="qwen2.5:7b" onChange={onChange} />)
    fireEvent.change(screen.getByLabelText(/model \(/i), { target: { value: 'llama3.2:3b' } })
    expect(onChange).toHaveBeenLastCalledWith('')
  })

  it('shows the unreachable notice only when reachable is false', () => {
    const { rerender } = render(
      <ModelSelector models={listing({ reachable: false })} value="" onChange={() => {}} />,
    )
    expect(screen.getByRole('status')).toHaveTextContent(/unreachable/i)
    rerender(<ModelSelector models={listing({ reachable: null })} value="" onChange={() => {}} />)
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })
})

describe('feature-root model wiring', () => {
  /** Routes the mount-time models GET and the assistant POST separately. */
  function routedFetch(models: AssistantModelsResponse | null) {
    return vi.fn().mockImplementation((_url: string, init?: RequestInit) =>
      Promise.resolve(
        init?.method === 'POST'
          ? jsonResponse(envelope())
          : models
            ? jsonResponse(models)
            : new Response('{}', { status: 404 }),
      ),
    )
  }

  function postCalls(fetchImpl: ReturnType<typeof vi.fn>) {
    return fetchImpl.mock.calls.filter(
      ([, init]) => (init as RequestInit | undefined)?.method === 'POST',
    )
  }

  it('fetches the listing on mount and sends the chosen model with the request', async () => {
    const fetchImpl = routedFetch(listing())
    render(<WritingDesignAssistant submitOptions={{ fetchImpl }} />)
    const select = await screen.findByLabelText(/model \(local-openai-compatible\)/i)
    fireEvent.change(select, { target: { value: 'qwen2.5:7b' } })
    fireEvent.change(screen.getByLabelText(/your text/i), { target: { value: 'hi' } })
    fireEvent.click(screen.getByRole('button', { name: /^send$/i }))
    await waitFor(() => expect(postCalls(fetchImpl)).toHaveLength(1))
    const body = JSON.parse((postCalls(fetchImpl)[0][1] as RequestInit).body as string)
    expect(body.model).toBe('qwen2.5:7b')
  })

  it('omits the model field entirely when the default stays selected', async () => {
    const fetchImpl = routedFetch(listing())
    render(<WritingDesignAssistant submitOptions={{ fetchImpl }} />)
    await screen.findByLabelText(/model \(local-openai-compatible\)/i)
    fireEvent.change(screen.getByLabelText(/your text/i), { target: { value: 'hi' } })
    fireEvent.click(screen.getByRole('button', { name: /^send$/i }))
    await waitFor(() => expect(postCalls(fetchImpl)).toHaveLength(1))
    const body = JSON.parse((postCalls(fetchImpl)[0][1] as RequestInit).body as string)
    expect('model' in body).toBe(false)
  })

  it('hides the selector when the listing is unavailable', async () => {
    const fetchImpl = routedFetch(null)
    render(<WritingDesignAssistant submitOptions={{ fetchImpl }} />)
    await waitFor(() =>
      expect(fetchImpl.mock.calls.some(([, init]) => (init as RequestInit | undefined)?.method !== 'POST')).toBe(true),
    )
    expect(screen.queryByLabelText(/model \(/i)).not.toBeInTheDocument()
  })
})
