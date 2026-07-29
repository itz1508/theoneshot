/**
 * assistant-results.test.tsx — result rendering per mode:
 * change list, three reply variants, teaching sections, kind-aware
 * diagram preview, copy actions, error/uncertainty states, and
 * history clear.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { WritingDesignAssistant } from '../WritingDesignAssistant'
import { AssistantResult } from '../components/AssistantResult'
import { InMemoryHistoryStore } from '../storage/history'
import type { AssistantResponse } from '../types'

vi.mock('mermaid', () => ({
  default: {
    initialize: vi.fn(),
    render: vi.fn().mockResolvedValue({ svg: '<svg role="presentation"></svg>' }),
  },
}))

const writeText = vi.fn().mockResolvedValue(undefined)

beforeEach(() => {
  writeText.mockClear()
  Object.defineProperty(navigator, 'clipboard', {
    value: { writeText },
    configurable: true,
  })
})

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

/**
 * Fetch stub that answers the mount-time models GET with 404 (selector
 * hidden) and every POST with a fresh Response carrying `body`.
 */
function routedFetch(body: unknown) {
  return vi.fn().mockImplementation((_url: string, init?: RequestInit) =>
    Promise.resolve(
      init?.method === 'POST'
        ? new Response(JSON.stringify(body), {
            status: 200,
            headers: { 'content-type': 'application/json' },
          })
        : new Response('{}', { status: 404 }),
    ),
  )
}

describe('result rendering', () => {
  it('renders the fix-wording change list', () => {
    render(
      <AssistantResult
        response={envelope({
          result: {
            corrected_text: 'I went home.',
            changes: [
              {
                original: 'goed',
                correction: 'went',
                reason: 'Irregular past tense.',
                intentional_possible: false,
              },
            ],
            no_changes_needed: false,
          },
        })}
      />,
    )
    expect(screen.getByText('goed')).toBeInTheDocument()
    expect(screen.getByText('went')).toBeInTheDocument()
    expect(screen.getByText(/irregular past tense/i)).toBeInTheDocument()
  })

  it('renders three reply variants', () => {
    render(
      <AssistantResult
        response={envelope({
          mode: 'draft_three_replies',
          result: {
            in_short: 'They want a date.',
            brief: 'Brief reply.',
            thorough: 'Thorough reply.',
            diplomatic: 'Diplomatic reply.',
            message_purpose: 'Scheduling',
            tone: 'neutral',
            uncertainty: [],
          },
        })}
      />,
    )
    expect(screen.getByRole('article', { name: /brief reply/i })).toBeInTheDocument()
    expect(screen.getByRole('article', { name: /thorough reply/i })).toBeInTheDocument()
    expect(screen.getByRole('article', { name: /diplomatic reply/i })).toBeInTheDocument()
    expect(screen.getByText('Brief reply.')).toBeInTheDocument()
  })

  it('renders teaching sections', () => {
    render(
      <AssistantResult
        response={envelope({
          mode: 'teach_clearly',
          result: {
            basics: 'Start here.',
            building_from_there: ['Step one.'],
            key_insights: ['Insight.'],
            common_misconceptions: ['Myth.'],
            why_this_matters: 'Because.',
            check_your_understanding: ['Question?'],
          },
        })}
      />,
    )
    expect(screen.getByText(/the basics/i)).toBeInTheDocument()
    expect(screen.getByText(/building from there/i)).toBeInTheDocument()
    expect(screen.getByText(/key insights/i)).toBeInTheDocument()
    expect(screen.getByText(/common misconceptions/i)).toBeInTheDocument()
    expect(screen.getByText(/why this matters/i)).toBeInTheDocument()
    expect(screen.getByText(/check your understanding/i)).toBeInTheDocument()
  })

  it('renders the workflow diagram preview with code and builder prompt', async () => {
    render(
      <AssistantResult
        response={envelope({
          mode: 'visualize_design',
          result: {
            kind: 'workflow',
            diagram_code: 'flowchart TD\n  A --> B',
            summary: 'Two connected boxes.',
            builder_prompt: 'Build two boxes.',
            warnings: [],
          },
        })}
      />,
    )
    expect(screen.getByText(/two connected boxes/i)).toBeInTheDocument()
    expect(screen.getByText(/flowchart TD/)).toBeInTheDocument()
    expect(screen.getByText('Build two boxes.')).toBeInTheDocument()
    await waitFor(() =>
      expect(screen.getByRole('img', { name: /design diagram preview/i })).toBeInTheDocument(),
    )
  })

  it('renders the layout preview with a collapsed/expanded toggle and no diagram', () => {
    render(
      <AssistantResult
        response={envelope({
          mode: 'visualize_design',
          result: {
            kind: 'layout',
            summary: 'A two-panel layout.',
            collapsed: ['App', '├─ Sidebar', '└─ Content'],
            expanded: ['App', '├─ Sidebar', '│  └─ Nav', '└─ Content', '   └─ Editor'],
            warnings: [],
          },
        })}
      />,
    )
    expect(screen.getByText(/a two-panel layout/i)).toBeInTheDocument()
    expect(screen.queryByRole('img', { name: /design diagram preview/i })).not.toBeInTheDocument()
    const tree = screen.getByLabelText('Layout tree')
    expect(tree).toHaveTextContent('├─ Sidebar')
    expect(tree).not.toHaveTextContent('Editor')

    fireEvent.click(screen.getByRole('button', { name: /show details/i }))
    expect(screen.getByLabelText('Layout tree')).toHaveTextContent('Editor')
    fireEvent.click(screen.getByRole('button', { name: /show overview/i }))
    expect(screen.getByLabelText('Layout tree')).not.toHaveTextContent('Editor')
  })

  it('renders an unclear result as summary and warnings only', () => {
    render(
      <AssistantResult
        response={envelope({
          mode: 'visualize_design',
          result: {
            kind: 'unclear',
            summary: 'Describe the screens or the steps involved.',
            warnings: ['The description names neither screens nor steps.'],
          },
        })}
      />,
    )
    expect(screen.getByText(/describe the screens/i)).toBeInTheDocument()
    expect(screen.getByLabelText('Diagram warnings')).toHaveTextContent(/names neither screens/i)
    expect(screen.queryByRole('img', { name: /design diagram preview/i })).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Layout tree')).not.toBeInTheDocument()
  })

  it('copy action writes the corrected text to the clipboard', async () => {
    render(<AssistantResult response={envelope({ result: { corrected_text: 'Copy me.' } })} />)
    fireEvent.click(screen.getByRole('button', { name: /copy improved message/i }))
    await waitFor(() => expect(writeText).toHaveBeenCalledWith('Copy me.'))
  })
})

describe('status states', () => {
  it('shows the normalized error state', async () => {
    const fetchImpl = routedFetch(
      envelope({
        status: 'failed',
        result: { error: { category: 'timeout', message: 'The model took too long.' } },
      }),
    )
    render(<WritingDesignAssistant submitOptions={{ fetchImpl }} />)
    fireEvent.change(screen.getByLabelText(/your text/i), { target: { value: 'hi' } })
    fireEvent.click(screen.getByRole('button', { name: /^send$/i }))
    expect(await screen.findByRole('alert')).toHaveTextContent(/timed out/i)
  })

  it('shows the uncertainty state', async () => {
    const fetchImpl = routedFetch(
      envelope({
        mode: 'expand_idea',
        status: 'uncertainty',
        result: {
          expanded_text: 'More.',
          preserved_intent: 'Same.',
          added_assumptions: [],
          uncertainty: ['Audience unclear.'],
        },
        uncertainty: ['Audience unclear.'],
      }),
    )
    render(<WritingDesignAssistant submitOptions={{ fetchImpl }} />)
    fireEvent.change(screen.getByLabelText(/your text/i), { target: { value: 'hi' } })
    fireEvent.click(screen.getByRole('button', { name: /^send$/i }))
    expect(await screen.findByText(/audience unclear/i)).toBeInTheDocument()
  })
})

describe('history', () => {
  it('records requests and clears them explicitly', async () => {
    const store = new InMemoryHistoryStore()
    const fetchImpl = routedFetch(envelope({}))
    render(<WritingDesignAssistant historyStore={store} submitOptions={{ fetchImpl }} />)
    fireEvent.change(screen.getByLabelText(/your text/i), { target: { value: 'remember me' } })
    fireEvent.click(screen.getByRole('button', { name: /^send$/i }))
    await waitFor(() => expect(store.list()).toHaveLength(1))

    fireEvent.click(screen.getByRole('button', { name: /toggle history/i }))
    const drawer = screen.getByRole('complementary', { name: /request history/i })
    expect(within(drawer).getByText(/remember me/i)).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /clear history/i }))
    expect(store.list()).toHaveLength(0)
    expect(screen.getByText(/no requests yet/i)).toBeInTheDocument()
  })
})
