import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { UsagePanel } from '../components/UsagePanel'
import type { UsageAccountingEvidence } from '../usageAccounting'

vi.mock('mermaid', () => ({
  default: {
    initialize: vi.fn(),
    render: vi.fn().mockResolvedValue({ svg: '<svg></svg>' }),
  },
}))

function evidence(
  overrides: Partial<UsageAccountingEvidence> = {},
): UsageAccountingEvidence {
  return {
    schema_version: '1.0.0',
    operation_id: 'op.1',
    attempt_id: 'attempt.1',
    provider: 'cloud-openai-compatible',
    model: 'fixture-model',
    accounting_status: 'complete',
    estimate: {
      input_tokens: 1200,
      reserved_output_tokens: 300,
      estimated_total_tokens: 1500,
      source: 'approximate',
      confidence: 'approximate',
      method: 'character_ratio',
    },
    actual: {
      input_tokens: 1100,
      output_tokens: 200,
      total_tokens: 1300,
      source: 'provider_reported',
      status: 'complete',
    },
    estimated_cost: {
      currency: 'USD',
      uncached_input: '0.002400',
      cached_read: '0.000000',
      cached_write: '0.000000',
      output: '0.002400',
      reasoning: '0.000000',
      total: '0.004800',
      pricing_record_id: 'fixture.v1',
      pricing_snapshot_sha256: `sha256:${'a'.repeat(64)}`,
    },
    actual_cost: null,
    warnings: ['actual usage is incomplete'],
    ...overrides,
  }
}

describe('UsagePanel', () => {
  it('keeps estimates and provider-reported actuals distinct', () => {
    render(<UsagePanel usage={evidence()} />)

    expect(screen.getByText('Estimated input')).toBeInTheDocument()
    expect(screen.getByText('1,200')).toBeInTheDocument()
    expect(screen.getByText('Provider-reported input')).toBeInTheDocument()
    expect(screen.getByText('1,100')).toBeInTheDocument()
    expect(screen.getByText('$0.004800 USD')).toBeInTheDocument()
    expect(screen.getByText('Pricing unavailable')).toBeInTheDocument()
  })

  it('marks LanguageTool token accounting as not applicable', () => {
    render(
      <UsagePanel
        usage={evidence({
          provider: 'languagetool',
          model: 'not_applicable',
          accounting_status: 'not_applicable',
          estimate: null,
          actual: null,
          warnings: [],
        })}
      />,
    )

    expect(
      screen.getByText('Token accounting does not apply to languagetool.'),
    ).toBeInTheDocument()
  })

  it('distinguishes unavailable tokens from zero tokens', () => {
    const { container } = render(
      <UsagePanel
        usage={evidence({
          actual: {
            input_tokens: null,
            output_tokens: null,
            cached_read_tokens: null,
            cache_write_tokens: null,
            source: 'unavailable',
            status: 'incomplete',
          },
        })}
      />,
    )

    // Find the value cell for "Provider-reported input" via its <dt> label.
    const dts = Array.from(container.querySelectorAll('dt'))
    const providerInputDt = dts.find((dt) =>
      dt.textContent?.trim() === 'Provider-reported input',
    )
    expect(providerInputDt).not.toBeNull()
    const valueCell = providerInputDt!.nextElementSibling
    expect(valueCell?.textContent?.trim()).toBe('Unavailable')
  })

  it('renders zero actual tokens distinctly from unknown actual tokens', () => {
    const { rerender, container } = render(
      <UsagePanel
        usage={evidence({
          actual: {
            input_tokens: 0,
            output_tokens: 0,
            total_tokens: 0,
            source: 'provider_reported',
            status: 'complete',
          },
        })}
      />,
    )
    // Zero tokens render as "0" (provider reported them as zero).
    const valueCellsZero = Array.from(container.querySelectorAll('dd')).map(
      (dd) => dd.textContent?.trim(),
    )
    expect(valueCellsZero.filter((t) => t === '0').length).toBeGreaterThanOrEqual(2)
    expect(valueCellsZero.filter((t) => t === 'Unavailable').length).toBe(0)

    rerender(
      <UsagePanel
        usage={evidence({
          actual: {
            input_tokens: null,
            output_tokens: null,
            source: 'unavailable',
            status: 'incomplete',
          },
        })}
      />,
    )
    // Unknown tokens render as "Unavailable", not "0".
    const valueCellsUnknown = Array.from(container.querySelectorAll('dd')).map(
      (dd) => dd.textContent?.trim(),
    )
    expect(valueCellsUnknown.filter((t) => t === 'Unavailable').length).toBeGreaterThanOrEqual(2)
    expect(valueCellsUnknown.filter((t) => t === '0').length).toBe(0)
  })

  it('surfaces unavailable exact cost separately from unavailable estimate', () => {
    render(
      <UsagePanel
        usage={evidence({
          estimated_cost: null,
          actual_cost: null,
        })}
      />,
    )

    // Both cost rows render "Pricing unavailable" — they are distinct
    // rows ("Estimated maximum charge" vs "Actual calculated charge").
    expect(screen.getByText('Estimated maximum charge')).toBeInTheDocument()
    expect(screen.getByText('Actual calculated charge')).toBeInTheDocument()
    const pricingUnavailables = screen.getAllByText('Pricing unavailable')
    expect(pricingUnavailables.length).toBe(2)
  })

  it('surfaces the accounting completeness status', () => {
    render(<UsagePanel usage={evidence({ accounting_status: 'incomplete' })} />)
    expect(screen.getByText('Accounting completeness')).toBeInTheDocument()
    expect(screen.getByText('incomplete')).toBeInTheDocument()
  })

  it('surfaces accounting warnings', () => {
    render(
      <UsagePanel
        usage={evidence({ warnings: ['native_usage_invalid', 'pricing_unavailable'] })}
      />,
    )
    expect(
      screen.getByText('native_usage_invalid · pricing_unavailable'),
    ).toBeInTheDocument()
  })

  it('labels the source of the actual usage (provider-reported)', () => {
    render(
      <UsagePanel
        usage={evidence({
          actual: {
            input_tokens: 10,
            output_tokens: 2,
            total_tokens: 12,
            source: 'provider_reported',
            status: 'complete',
          },
        })}
      />,
    )
    // "Provider-reported input" / "Provider-reported output" labels
    // identify the actual usage source; they remain visible regardless
    // of whether the source was native or compatibility fallback — the
    // panel does not silently re-label the source.
    expect(screen.getByText('Provider-reported input')).toBeInTheDocument()
    expect(screen.getByText('Provider-reported output')).toBeInTheDocument()
  })

  it('displays "Not reserved" when estimate is null (no reservation was made)', () => {
    const { container } = render(
      <UsagePanel usage={evidence({ estimate: null })} />,
    )
    const dts = Array.from(container.querySelectorAll('dt'))
    const reservedDt = dts.find((dt) => dt.textContent?.trim() === 'Reserved output')
    expect(reservedDt).not.toBeNull()
    const valueCell = reservedDt!.nextElementSibling
    // When the entire estimate is absent, no reservation exists.
    expect(valueCell?.textContent?.trim()).toBe('Not reserved')
  })

  it('renders zero as a valid reservation, distinct from absence', () => {
    const { container } = render(
      <UsagePanel
        usage={evidence({
          estimate: {
            input_tokens: 500,
            reserved_output_tokens: 0,
            estimated_total_tokens: 500,
            source: 'approximate',
            confidence: 'approximate',
            method: 'character_ratio',
          },
        })}
      />,
    )
    const dts = Array.from(container.querySelectorAll('dt'))
    const reservedDt = dts.find((dt) => dt.textContent?.trim() === 'Reserved output')
    expect(reservedDt).not.toBeNull()
    const valueCell = reservedDt!.nextElementSibling
    // Zero is a valid reservation (user configured max_output_tokens=0),
    // NOT absence. It must render as "0", not "Not reserved".
    expect(valueCell?.textContent?.trim()).toBe('0')
  })

  it('displays the numeric reservation when a meaningful reservation exists', () => {
    const { container } = render(<UsagePanel usage={evidence()} />)
    const dts = Array.from(container.querySelectorAll('dt'))
    const reservedDt = dts.find((dt) => dt.textContent?.trim() === 'Reserved output')
    expect(reservedDt).not.toBeNull()
    const valueCell = reservedDt!.nextElementSibling
    // evidence() default carries reserved_output_tokens: 300.
    expect(valueCell?.textContent?.trim()).toBe('300')
  })
})
