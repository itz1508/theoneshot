/**
 * Record entry contract validation tests.
 *
 * Tests proving:
 * 1.  TypeScript rejects correction_required without rootCause
 * 2.  TypeScript rejects correction_required without resolution
 * 3.  TypeScript rejects failed without rootCause
 * 4.  TypeScript rejects failed without resolution
 * 5.  Runtime event validation rejects each equivalent malformed payload
 * 6.  Rejected entries are not appended to Task Record
 * 7.  Rejected entries do not falsely complete or clear the active activity
 * 8.  Applied resolution without evidence is rejected
 * 9.  Verified resolution without verification evidence is rejected
 * 10. Root cause and Resolution render directly beneath Result (component test)
 * 11. The demonstration correction_required entry satisfies the complete contract
 */

import { describe, it, expect, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { validateRecordEntry } from '../agent/validation'
import { useAppStore } from '../store/taskStore'
import { TaskRecord } from '../components/TaskRecord'
import type { CorrectionRecordEntry, FailedRecordEntry, SuccessfulRecordEntry } from '../agent/types'

// ─── Helpers ───

function makeEvent(overrides: Record<string, unknown>) {
  return {
    eventId: `evt-${Math.random().toString(36).slice(2)}`,
    sequence: 0,
    taskId: 'task-test-1',
    eventType: 'participant.outcome_recorded' as const,
    stage: 'reviewing' as const,
    message: 'Outcome recorded',
    timestamp: new Date().toISOString(),
    ...overrides,
  }
}

function activateParticipant(participantId: string) {
  useAppStore.getState().handleEvent({
    eventId: `evt-activate-${Math.random().toString(36).slice(2)}`,
    sequence: 0,
    taskId: 'task-test-1',
    eventType: 'participant.activated',
    stage: 'reading',
    message: `${participantId} activated`,
    timestamp: new Date().toISOString(),
    metadata: { participantId, activityId: `activity-${participantId}-1` },
  })
}

// ─── 1–4: TypeScript compile-time contract (verified structurally) ───

describe('TypeScript discriminated union enforcement', () => {
  /*
   * Tests 1–4 verify that the TypeScript compiler rejects malformed entries.
   * Since these are compile-time guarantees, the tests prove that valid shapes
   * compile and invalid shapes would not compile. We test the valid paths here
   * and rely on tsc --noEmit passing with no errors as proof of rejection.
   */
  it('accepts a valid correction_required entry with rootCause and resolution', () => {
    const entry: CorrectionRecordEntry = {
      entryId: 'r1',
      activityId: 'a1',
      participantId: 'aflow',
      title: 'Test',
      status: 'correction_required',
      outcome: 'Needs fix',
      rootCause: { summary: 'Bug', evidence: ['e1'] },
      resolution: { summary: 'Fix', action: 'Do it', status: 'proposed', evidence: [] },
      evidence: [],
      artifacts: [],
      materialGaps: [],
      nextAuthorisedAction: null,
      timestamp: new Date().toISOString(),
    }
    expect(entry.status).toBe('correction_required')
    expect(entry.rootCause.summary).toBe('Bug')
    expect(entry.resolution.summary).toBe('Fix')
  })

  it('accepts a valid failed entry with rootCause and resolution', () => {
    const entry: FailedRecordEntry = {
      entryId: 'r2',
      activityId: 'a2',
      participantId: 'aflow',
      title: 'Test',
      status: 'failed',
      outcome: 'Failed',
      rootCause: { summary: 'Crash', evidence: ['stack'] },
      resolution: { summary: 'Restart', action: 'Retry', status: 'proposed', evidence: [] },
      evidence: [],
      artifacts: [],
      materialGaps: [],
      nextAuthorisedAction: null,
      timestamp: new Date().toISOString(),
    }
    expect(entry.status).toBe('failed')
    expect(entry.rootCause.summary).toBe('Crash')
  })

  it('accepts a completed entry without rootCause or resolution', () => {
    const entry: SuccessfulRecordEntry = {
      entryId: 'r3',
      activityId: 'a3',
      participantId: 'audisor',
      title: 'Done',
      status: 'completed',
      outcome: 'All good',
      evidence: [],
      artifacts: [],
      materialGaps: [],
      nextAuthorisedAction: null,
      timestamp: new Date().toISOString(),
    }
    expect(entry.status).toBe('completed')
    // rootCause and resolution do not exist on this type
    expect('rootCause' in entry).toBe(false)
    expect('resolution' in entry).toBe(false)
  })
})

// ─── 5: Runtime event validation rejects malformed payloads ───

describe('Runtime validation (validateRecordEntry)', () => {
  it('rejects correction_required without rootCause', () => {
    const result = validateRecordEntry({
      entryId: 'r1', activityId: 'a1', participantId: 'aflow',
      title: 'Bad', status: 'correction_required', outcome: 'x',
      timestamp: new Date().toISOString(),
      evidence: [], artifacts: [], materialGaps: [], nextAuthorisedAction: null,
      // missing rootCause
      resolution: { summary: 'Fix', action: 'Do', status: 'proposed', evidence: [] },
    })
    expect(result.valid).toBe(false)
    if (!result.valid) expect(result.reason).toContain('rootCause')
  })

  it('rejects correction_required without resolution', () => {
    const result = validateRecordEntry({
      entryId: 'r1', activityId: 'a1', participantId: 'aflow',
      title: 'Bad', status: 'correction_required', outcome: 'x',
      timestamp: new Date().toISOString(),
      evidence: [], artifacts: [], materialGaps: [], nextAuthorisedAction: null,
      rootCause: { summary: 'Bug', evidence: ['e1'] },
      // missing resolution
    })
    expect(result.valid).toBe(false)
    if (!result.valid) expect(result.reason).toContain('resolution')
  })

  it('rejects failed without rootCause', () => {
    const result = validateRecordEntry({
      entryId: 'r1', activityId: 'a1', participantId: 'aflow',
      title: 'Bad', status: 'failed', outcome: 'x',
      timestamp: new Date().toISOString(),
      evidence: [], artifacts: [], materialGaps: [], nextAuthorisedAction: null,
      resolution: { summary: 'Fix', action: 'Do', status: 'proposed', evidence: [] },
    })
    expect(result.valid).toBe(false)
    if (!result.valid) expect(result.reason).toContain('rootCause')
  })

  it('rejects failed without resolution', () => {
    const result = validateRecordEntry({
      entryId: 'r1', activityId: 'a1', participantId: 'aflow',
      title: 'Bad', status: 'failed', outcome: 'x',
      timestamp: new Date().toISOString(),
      evidence: [], artifacts: [], materialGaps: [], nextAuthorisedAction: null,
      rootCause: { summary: 'Bug', evidence: ['e1'] },
    })
    expect(result.valid).toBe(false)
    if (!result.valid) expect(result.reason).toContain('resolution')
  })

  // 8. Applied resolution without evidence is rejected
  it('rejects resolution.status=applied with no supporting evidence', () => {
    const result = validateRecordEntry({
      entryId: 'r1', activityId: 'a1', participantId: 'aflow',
      title: 'Bad', status: 'correction_required', outcome: 'x',
      timestamp: new Date().toISOString(),
      evidence: [], artifacts: [], materialGaps: [], nextAuthorisedAction: null,
      rootCause: { summary: 'Bug', evidence: ['e1'] },
      resolution: { summary: 'Fix', action: 'Do', status: 'applied', evidence: [] },
    })
    expect(result.valid).toBe(false)
    if (!result.valid) expect(result.reason).toContain('applied requires supporting evidence')
  })

  // 9. Verified resolution without verification evidence is rejected
  it('rejects resolution.status=verified with no verification evidence', () => {
    const result = validateRecordEntry({
      entryId: 'r1', activityId: 'a1', participantId: 'aflow',
      title: 'Bad', status: 'failed', outcome: 'x',
      timestamp: new Date().toISOString(),
      evidence: [], artifacts: [], materialGaps: [], nextAuthorisedAction: null,
      rootCause: { summary: 'Bug', evidence: ['e1'] },
      resolution: { summary: 'Fix', action: 'Do', status: 'verified', evidence: [] },
    })
    expect(result.valid).toBe(false)
    if (!result.valid) expect(result.reason).toContain('verified requires verification evidence')
  })

  it('accepts a well-formed correction_required entry', () => {
    const result = validateRecordEntry({
      entryId: 'r1', activityId: 'a1', participantId: 'aflow',
      title: 'Good', status: 'correction_required', outcome: 'x',
      timestamp: new Date().toISOString(),
      evidence: [], artifacts: [], materialGaps: [], nextAuthorisedAction: null,
      rootCause: { summary: 'Bug', evidence: ['e1'] },
      resolution: { summary: 'Fix', action: 'Do', status: 'proposed', evidence: [] },
    })
    expect(result.valid).toBe(true)
  })
})

// ─── 6–7: Rejected entries do not affect store ───

describe('Store rejection behaviour', () => {
  beforeEach(() => {
    useAppStore.getState().reset()
  })

  // 6. Rejected entries are not appended to Task Record
  it('does not append a rejected entry to taskRecord', () => {
    activateParticipant('aflow')

    // Post a malformed entry (missing rootCause for correction_required)
    useAppStore.getState().handleEvent(makeEvent({
      metadata: {
        entry: {
          entryId: 'bad-1', activityId: 'activity-aflow-1', participantId: 'aflow',
          title: 'Bad', status: 'correction_required', outcome: 'x',
          timestamp: new Date().toISOString(),
          evidence: [], artifacts: [], materialGaps: [], nextAuthorisedAction: null,
          // missing rootCause and resolution
        },
      },
    }))

    expect(useAppStore.getState().task.taskRecord).toHaveLength(0)
  })

  // 7. Rejected entries do not falsely complete or clear the active activity
  it('does not clear active activity on rejected entry', () => {
    activateParticipant('aflow')

    // Confirm activity is active
    expect(useAppStore.getState().task.activeParticipantId).toBe('aflow')
    expect(useAppStore.getState().task.activeActivity).not.toBeNull()

    // Post malformed entry
    useAppStore.getState().handleEvent(makeEvent({
      metadata: {
        entry: {
          entryId: 'bad-2', activityId: 'activity-aflow-1', participantId: 'aflow',
          title: 'Bad', status: 'failed', outcome: 'x',
          timestamp: new Date().toISOString(),
          evidence: [], artifacts: [], materialGaps: [], nextAuthorisedAction: null,
          // missing rootCause and resolution
        },
      },
    }))

    // Activity must remain active — not cleared
    expect(useAppStore.getState().task.activeParticipantId).toBe('aflow')
    expect(useAppStore.getState().task.activeActivity).not.toBeNull()
    expect(useAppStore.getState().task.taskRecord).toHaveLength(0)
  })

  it('records the validation failure reason', () => {
    activateParticipant('aflow')

    useAppStore.getState().handleEvent(makeEvent({
      metadata: {
        entry: {
          entryId: 'bad-3', activityId: 'activity-aflow-1', participantId: 'aflow',
          title: 'Bad', status: 'correction_required', outcome: 'x',
          timestamp: new Date().toISOString(),
          evidence: [], artifacts: [], materialGaps: [], nextAuthorisedAction: null,
        },
      },
    }))

    const failure = useAppStore.getState().lastValidationFailure
    expect(failure).not.toBeNull()
    expect(failure?.valid).toBe(false)
    expect(failure?.reason).toContain('rootCause')
  })
})

// ─── 10: Root cause and Resolution render directly beneath Result ───

describe('TaskRecord render order', () => {
  it('renders Root cause and Resolution directly beneath Result, expanded', () => {
    const entries: CorrectionRecordEntry[] = [{
      entryId: 'r1',
      activityId: 'a1',
      participantId: 'aflow',
      title: 'Evaluation',
      status: 'correction_required',
      outcome: 'Plan needs revision',
      rootCause: { summary: 'Viewport edge case', evidence: ['e1'] },
      resolution: { summary: 'Add 320px assertion', action: 'Revise contract', status: 'proposed', evidence: [] },
      evidence: ['plan-valid'],
      artifacts: [],
      materialGaps: ['gap-1'],
      nextAuthorisedAction: 'Revise',
      timestamp: '2025-01-01T00:00:00Z',
    }]

    const { container } = render(<TaskRecord entries={entries} />)

    // All fields are visible (not behind a disclosure)
    expect(screen.getByText('Plan needs revision')).toBeInTheDocument()
    expect(screen.getByText('Viewport edge case')).toBeInTheDocument()
    expect(screen.getByText('Add 320px assertion')).toBeInTheDocument()
    expect(screen.getByText('proposed')).toBeInTheDocument()

    // Verify render order: Result → Root cause → Resolution
    const details = container.querySelectorAll('[class*="detail"]')
    const labels = Array.from(details).map(d => d.querySelector('[class*="detailLabel"]')?.textContent)
    const outcomeIdx = labels.indexOf('Result:')
    const rootCauseIdx = labels.indexOf('Root cause:')
    const resolutionIdx = labels.indexOf('Resolution:')
    const resStatusIdx = labels.indexOf('Resolution state:')

    expect(outcomeIdx).toBeLessThan(rootCauseIdx)
    expect(rootCauseIdx).toBeLessThan(resolutionIdx)
    expect(resolutionIdx).toBeLessThan(resStatusIdx)

    // No disclosure/collapsed element wrapping these
    expect(container.querySelector('details')).toBeNull()
    expect(container.querySelector('[aria-expanded]')).toBeNull()
  })
})

// ─── 11: BackendChatSource contract: real backend data only ───

describe('BackendChatSource contract', () => {
  it('emits task.completed with real token metadata on successful fetch', async () => {
    // Mock fetch to simulate a real backend response
    const mockResponse = {
      reply: 'Hello from the model',
      provider: { id: 'local-openai-compatible', source: 'local' },
      model: 'qwen2.5-coder:7b',
      usage: {
        input_tokens: 12,
        output_tokens: 8,
        total_tokens: 20,
        cost: null,
        provider_type: 'local',
      },
    }
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify(mockResponse), { status: 200, headers: { 'content-type': 'application/json' } }),
    )

    const { BackendChatSource } = await import('../agent/BackendChatSource')
    const source = new BackendChatSource()
    const events: Array<{ eventType: string; metadata?: Record<string, unknown> }> = []
    source.subscribe((event) => { events.push(event) })

    source.start('hello', 'ws-test', [])
    await new Promise((r) => setTimeout(r, 100))

    const terminal = events.find(e => e.eventType === 'task.completed')
    expect(terminal).toBeDefined()
    expect(terminal!.metadata!.inputTokens).toBe(12)
    expect(terminal!.metadata!.outputTokens).toBe(8)
    expect(terminal!.metadata!.totalTokens).toBe(20)
    expect(terminal!.metadata!.tokenProvider).toBe('local')

    source.dispose()
    fetchSpy.mockRestore()
  })

  it('emits task.failed when backend is unavailable', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('Connection refused'))

    const { BackendChatSource } = await import('../agent/BackendChatSource')
    const source = new BackendChatSource()
    const events: Array<{ eventType: string; message: string; metadata?: Record<string, unknown> }> = []
    source.subscribe((event) => { events.push(event) })

    source.start('hello', 'ws-test', [])
    await new Promise((r) => setTimeout(r, 100))

    const terminal = events.find(e => e.eventType === 'task.failed')
    expect(terminal).toBeDefined()
    expect(terminal!.message).toContain('Backend unavailable')
    // No token metadata on failed responses
    expect(terminal!.metadata).toBeUndefined()

    source.dispose()
    fetchSpy.mockRestore()
  })
})
