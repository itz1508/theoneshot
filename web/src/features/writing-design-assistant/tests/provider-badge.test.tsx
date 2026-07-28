/**
 * provider-badge.test.tsx — provenance badge regression tests.
 *
 * Root-cause context: a stale backend serving the fake-deterministic
 * provider was invisible in the UI, so canned answers looked like model
 * output. The badge makes the reported provider visible on every result
 * and failure banner, and must stay null-safe.
 */

import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { AssistantResult, ProviderBadge } from '../components/AssistantResult'
import { AssistantStatus } from '../components/AssistantStatus'
import type { AssistantResponse } from '../types'

function envelope(overrides: Partial<AssistantResponse>): AssistantResponse {
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

describe('provider provenance badge', () => {
  it('renders the fake provider id on a completed result', () => {
    render(<AssistantResult response={envelope({})} />)
    expect(screen.getByTestId('provider-badge')).toHaveTextContent('fake-deterministic · local')
  })

  it('renders the real local provider id', () => {
    render(
      <AssistantResult
        response={envelope({ provider: { id: 'local-openai-compatible', source: 'local' } })}
      />,
    )
    expect(screen.getByTestId('provider-badge')).toHaveTextContent(
      'local-openai-compatible · local',
    )
  })

  it('is null-safe when the envelope carries no provider', () => {
    render(<AssistantResult response={envelope({ provider: null })} />)
    expect(screen.queryByTestId('provider-badge')).not.toBeInTheDocument()
  })

  it('renders nothing for an empty provider id', () => {
    render(<ProviderBadge provider={{ id: '', source: 'local' }} />)
    expect(screen.queryByTestId('provider-badge')).not.toBeInTheDocument()
  })

  it('shows the provider on a failed envelope', () => {
    render(
      <AssistantStatus
        loading={false}
        response={envelope({
          status: 'failed',
          result: { error: { category: 'timeout', message: 'The model took too long.' } },
        })}
      />,
    )
    expect(screen.getByTestId('provider-badge')).toHaveTextContent('fake-deterministic · local')
  })

  it('shows the executing engine when the envelope carries it', () => {
    render(
      <AssistantResult
        response={envelope({
          provider: { id: 'languagetool', source: 'local' },
          engine: 'languagetool',
          result: { corrected_text: 'Fixed.', result_kind: 'languagetool' },
        })}
      />,
    )
    expect(screen.getByTestId('provider-badge')).toHaveTextContent(
      'languagetool · local · languagetool',
    )
  })

  it('marks a fallback-served result as fallback', () => {
    render(
      <AssistantResult
        response={envelope({
          provider: { id: 'local-openai-compatible', source: 'local' },
          engine: 'model',
          fallback_used: true,
          fallback_reason: 'The grammar checker is unavailable.',
        })}
      />,
    )
    expect(screen.getByTestId('provider-badge')).toHaveTextContent(
      'local-openai-compatible · local · model · fallback',
    )
  })

  it('omits engine and fallback markers when the envelope lacks them', () => {
    render(<AssistantResult response={envelope({})} />)
    const badge = screen.getByTestId('provider-badge')
    expect(badge.textContent).not.toContain('fallback')
    expect(badge.textContent).toBe('fake-deterministic · local')
  })
})
