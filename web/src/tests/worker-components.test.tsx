/**
 * Component-level tests for the participant ownership UI contract.
 *
 * 13. Reduced-motion mode does not use the 3D flip
 * 14. Visual components do not instantiate DemoTaskEventSource directly
 */

import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { ParticipantHeader } from '../components/ParticipantHeader'
import { LiveActivity } from '../components/LiveActivity'
import { TaskRecord } from '../components/TaskRecord'
import { TaskReviewDrawer } from '../components/TaskReviewDrawer'
import type { TaskState, CorrectionRecordEntry, SuccessfulRecordEntry } from '../agent/types'
import { initialTask } from '../store/taskStore'
import { readFileSync } from 'node:fs'
import { resolve, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = dirname(fileURLToPath(import.meta.url))

// ─── 13. Reduced-motion mode does not use the 3D flip ───

describe('ParticipantHeader accessibility', () => {
  it('static stylesheet contract: reduced-motion rule neutralizes 3D transform', () => {
    // This is a static stylesheet contract test — it verifies the CSS source
    // contains the prefers-reduced-motion rule with transform: none.
    // It does not render the component under reduced-motion conditions.
    const cssPath = resolve(__dirname, '../components/ParticipantHeader.module.css')
    const cssContent = readFileSync(cssPath, 'utf-8')

    // Must contain a prefers-reduced-motion media query
    expect(cssContent).toContain('prefers-reduced-motion')

    // The reduced-motion block must not apply rotateX
    const reducedMotionBlock = cssContent.split('@media (prefers-reduced-motion: reduce)')[1]
    expect(reducedMotionBlock).toBeDefined()
    // Should remove or neutralize transform
    expect(reducedMotionBlock).toContain('transform: none')
  })

  it('renders participant name and status correctly', () => {
    render(<ParticipantHeader participantId="audisor" status="working" summary="Testing" />)
    expect(screen.getByText('Audisor')).toBeInTheDocument()
    expect(screen.getByText('Working')).toBeInTheDocument()
    expect(screen.getByText('Testing')).toBeInTheDocument()
  })

  it('shows empty state when no participant is active', () => {
    render(<ParticipantHeader participantId={null} status="idle" summary="" />)
    expect(screen.getByText('No active participant')).toBeInTheDocument()
  })
})

// ─── 14. Visual components do not instantiate DemoTaskEventSource directly ───

describe('Component isolation from DemoTaskEventSource', () => {
  const componentFiles = [
    '../components/ParticipantHeader.tsx',
    '../components/LiveActivity.tsx',
    '../components/TaskRecord.tsx',
    '../components/TaskReviewDrawer.tsx',
  ]

  it.each(componentFiles)('%s does not import DemoTaskEventSource', (relPath) => {
    const filePath = resolve(__dirname, relPath)
    const content = readFileSync(filePath, 'utf-8')
    expect(content).not.toContain('DemoTaskEventSource')
  })
})

// ─── LiveActivity rendering ───

describe('LiveActivity', () => {
  it('renders nothing when messages array is empty', () => {
    const { container } = render(<LiveActivity messages={[]} />)
    expect(container.innerHTML).toBe('')
  })

  it('renders each activity update text', () => {
    const messages = [
      { id: 'm1', text: 'Inspecting structure', timestamp: '2025-01-01T00:00:00Z' },
      { id: 'm2', text: 'Preparing plan', timestamp: '2025-01-01T00:00:01Z' },
    ]
    render(<LiveActivity messages={messages} />)
    expect(screen.getByText('Inspecting structure')).toBeInTheDocument()
    expect(screen.getByText('Preparing plan')).toBeInTheDocument()
  })
})

// ─── TaskRecord rendering ───

describe('TaskRecord', () => {
  it('renders nothing when entries is empty', () => {
    const { container } = render(<TaskRecord entries={[]} />)
    expect(container.innerHTML).toBe('')
  })

  it('renders recorded outcomes with root cause and resolution', () => {
    const entries: CorrectionRecordEntry[] = [{
      entryId: 'r1',
      activityId: 'a1',
      participantId: 'aflow',
      title: 'A-Flow evaluation',
      status: 'correction_required',
      outcome: 'Needs revision',
      rootCause: { summary: 'Missing contract', evidence: ['e1'] },
      resolution: { summary: 'Add contract', action: 'Revise', status: 'proposed', evidence: [] },
      evidence: [],
      artifacts: [],
      materialGaps: ['gap-1'],
      nextAuthorisedAction: 'Revise plan',
      timestamp: '2025-01-01T00:00:00Z',
    }]
    render(<TaskRecord entries={entries} />)
    expect(screen.getByText('A-Flow evaluation')).toBeInTheDocument()
    expect(screen.getByText('Needs revision')).toBeInTheDocument()
    expect(screen.getByText('Missing contract')).toBeInTheDocument()
    expect(screen.getByText(/Add contract/)).toBeInTheDocument()
    expect(screen.getByText('Revise plan')).toBeInTheDocument()
  })

  it('does not show root cause for completed entries', () => {
    const entries: SuccessfulRecordEntry[] = [{
      entryId: 'r2',
      activityId: 'a2',
      participantId: 'audisor',
      title: 'Completed task',
      status: 'completed',
      outcome: 'All good',
      evidence: [],
      artifacts: [],
      materialGaps: [],
      nextAuthorisedAction: null,
      timestamp: '2025-01-01T00:00:00Z',
    }]
    render(<TaskRecord entries={entries} />)
    expect(screen.getByText('Completed task')).toBeInTheDocument()
    expect(screen.queryByText('Root cause:')).not.toBeInTheDocument()
  })
})

// ─── TaskReviewDrawer composition ───

describe('TaskReviewDrawer', () => {
  const taskWithParticipant: TaskState = {
    ...initialTask,
    taskId: 'task-1',
    status: 'running',
    objective: 'Test objective',
    activeParticipantId: 'audisor',
    activeActivityId: 'act-1',
    activeActivity: {
      activityId: 'act-1',
      participantId: 'audisor',
      status: 'working',
      summary: 'Testing things',
      messages: [
        { id: 'm1', text: 'Step one', timestamp: '2025-01-01T00:00:00Z' },
      ],
    },
    taskRecord: [],
  }

  it('renders the active participant header within the drawer', () => {
    render(
      <TaskReviewDrawer
        open={true}
        task={taskWithParticipant}
        runnerMode="Demonstration events · no backend execution"
        onToggle={() => {}}
        onCancel={() => {}}
      />
    )
    expect(screen.getByText('Audisor')).toBeInTheDocument()
    expect(screen.getByText('Working')).toBeInTheDocument()
  })

  it('does not display tabs or lifecycle strip', () => {
    const { container } = render(
      <TaskReviewDrawer
        open={true}
        task={taskWithParticipant}
        runnerMode="Demonstration events · no backend execution"
        onToggle={() => {}}
        onCancel={() => {}}
      />
    )
    // No tabs
    expect(container.querySelector('[role="tablist"]')).toBeNull()
    expect(container.querySelector('[role="tab"]')).toBeNull()
    // No lifecycle strip text
    expect(screen.queryByText('A-Flow')).not.toBeInTheDocument()
  })
})
