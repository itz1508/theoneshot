/**
 * DemoTaskEventSource — deterministic local mock implementing TaskEventSource.
 *
 * Demonstrates the full participant-ownership flow:
 *   Audisor active → delegates to A-Flow → A-Flow records outcome → Audisor resumes
 *
 * Read-only: never mutates any file or calls any external service.
 * Identified as "Demonstration events · no backend execution".
 */

import type { AgentEvent, Stage, EventType, CorrectionRecordEntry, SuccessfulRecordEntry } from './types'
import type { TaskEventSource, EventListener } from './TaskEventSource'

interface EventStep {
  eventType: EventType
  stage: Stage
  workspaceId?: string
  filePath?: string
  message: string
  delayMs: number
  metadata?: Record<string, unknown>
}

let nextId = 0
function uid(): string {
  return `evt-${Date.now()}-${nextId++}`
}

export class DemoTaskEventSource implements TaskEventSource {
  private listeners = new Set<EventListener>()
  private timers: ReturnType<typeof setTimeout>[] = []
  private cancelled = false
  private currentTaskId: string | null = null

  start(_message: string, primaryWorkspaceId: string, _linkedWorkspaceIds: string[]): string {
    this.cancel()

    const taskId = `task-${Date.now()}`
    this.currentTaskId = taskId
    this.cancelled = false

    const steps = this.buildOwnershipSequence(primaryWorkspaceId)
    let seq = 0
    let cumulativeDelay = 0

    for (const step of steps) {
      cumulativeDelay += step.delayMs
      const capturedSeq = seq++
      const timer = setTimeout(() => {
        if (this.cancelled) return
        const event: AgentEvent = {
          eventId: uid(),
          sequence: capturedSeq,
          taskId,
          eventType: step.eventType,
          stage: step.stage,
          workspaceId: step.workspaceId,
          filePath: step.filePath,
          message: step.message,
          timestamp: new Date().toISOString(),
          metadata: step.metadata,
        }
        this.emit(event)
      }, cumulativeDelay)
      this.timers.push(timer)
    }

    return taskId
  }

  subscribe(listener: EventListener): () => void {
    this.listeners.add(listener)
    return () => { this.listeners.delete(listener) }
  }

  cancel(): void {
    if (this.cancelled) return
    this.cancelled = true
    for (const t of this.timers) clearTimeout(t)
    this.timers = []

    if (this.currentTaskId) {
      const event: AgentEvent = {
        eventId: uid(),
        sequence: 999,
        taskId: this.currentTaskId,
        eventType: 'task.cancelled',
        stage: 'cancelled',
        message: 'Task cancelled by operator',
        timestamp: new Date().toISOString(),
      }
      this.emit(event)
    }
    this.currentTaskId = null
  }

  dispose(): void {
    this.cancel()
    this.listeners.clear()
  }

  private emit(event: AgentEvent): void {
    for (const listener of this.listeners) {
      listener(event)
    }
  }

  private buildOwnershipSequence(primaryWorkspaceId: string): EventStep[] {
    const audisorActivityId = 'activity-audisor-1'
    const aflowActivityId = 'activity-aflow-1'
    const audisorResumeActivityId = 'activity-audisor-2'

    const aflowEntry: CorrectionRecordEntry = {
      entryId: 'entry-aflow-1',
      activityId: aflowActivityId,
      participantId: 'aflow',
      title: 'A-Flow evaluation',
      status: 'correction_required',
      outcome: 'Plan requires resize validation contract revision',
      rootCause: {
        summary: 'Resize validation contract does not cover edge-case viewport below 320px',
        evidence: ['Explorer collapse test missing', 'No min-width assertion in contract'],
      },
      resolution: {
        summary: 'Add min-width 320px viewport assertion to resize validation',
        action: 'Revise the resize validation contract',
        status: 'proposed',
        evidence: [],
      },
      evidence: ['plan-structure-valid', 'evidence-refs-resolved'],
      artifacts: [],
      materialGaps: ['Resize validation contract incomplete for sub-320px viewports'],
      nextAuthorisedAction: 'Revise the resize validation contract',
      timestamp: new Date().toISOString(),
    }

    const audisorFinalEntry: SuccessfulRecordEntry = {
      entryId: 'entry-audisor-1',
      activityId: audisorResumeActivityId,
      participantId: 'audisor',
      title: 'Implementation completed',
      status: 'completed',
      outcome: 'Task plan revised and implementation applied successfully',
      evidence: ['resize-contract-updated', 'validation-passed'],
      artifacts: ['src/demo_output.py'],
      materialGaps: [],
      nextAuthorisedAction: null,
      timestamp: new Date().toISOString(),
    }

    const steps: EventStep[] = [
      // 1. Task created → Audisor becomes active
      {
        eventType: 'task.created',
        stage: 'queued',
        workspaceId: primaryWorkspaceId,
        message: 'Task queued',
        delayMs: 300,
      },
      {
        eventType: 'participant.activated',
        stage: 'reading',
        workspaceId: primaryWorkspaceId,
        message: 'Audisor activated',
        delayMs: 400,
        metadata: { participantId: 'audisor', activityId: audisorActivityId },
      },

      // 2. Audisor activity updates
      {
        eventType: 'participant.activity_update',
        stage: 'reading',
        workspaceId: primaryWorkspaceId,
        message: 'Inspecting repository structure',
        delayMs: 800,
      },
      {
        eventType: 'workspace.entered',
        stage: 'reading',
        workspaceId: primaryWorkspaceId,
        message: 'Entered Theoneshot',
        delayMs: 600,
      },
      {
        eventType: 'participant.activity_update',
        stage: 'planning',
        workspaceId: primaryWorkspaceId,
        message: 'Preparing candidate plan',
        delayMs: 1000,
      },
      {
        eventType: 'participant.activity_update',
        stage: 'planning',
        workspaceId: primaryWorkspaceId,
        message: 'Delegating plan evaluation to A-Flow',
        delayMs: 800,
      },

      // 3. Ownership changes to A-Flow
      {
        eventType: 'participant.activated',
        stage: 'reviewing',
        message: 'A-Flow evaluation started',
        delayMs: 600,
        metadata: { participantId: 'aflow', activityId: aflowActivityId },
      },

      // 4–5. A-Flow activity updates
      {
        eventType: 'participant.activity_update',
        stage: 'reviewing',
        message: 'Validating plan structure',
        delayMs: 900,
      },
      {
        eventType: 'participant.activity_update',
        stage: 'reviewing',
        message: 'Checking evidence references',
        delayMs: 800,
      },
      {
        eventType: 'participant.activity_update',
        stage: 'reviewing',
        message: 'Resolving one material gap',
        delayMs: 700,
      },
      {
        eventType: 'participant.activity_update',
        stage: 'reviewing',
        message: 'Recording evaluation outcome',
        delayMs: 600,
      },

      // 6. A-Flow records structured outcome (clears A-Flow, returns ownership)
      {
        eventType: 'participant.outcome_recorded',
        stage: 'reviewing',
        message: 'A-Flow evaluation complete',
        delayMs: 500,
        metadata: { entry: aflowEntry },
      },

      // 8–9. Ownership returns to Audisor
      {
        eventType: 'participant.activated',
        stage: 'editing',
        workspaceId: primaryWorkspaceId,
        message: 'Audisor resumed',
        delayMs: 600,
        metadata: { participantId: 'audisor', activityId: audisorResumeActivityId },
      },

      // 10. Audisor activity updates
      {
        eventType: 'participant.activity_update',
        stage: 'editing',
        workspaceId: primaryWorkspaceId,
        message: 'Applying the A-Flow result to the task plan',
        delayMs: 900,
      },
      {
        eventType: 'file.changed',
        stage: 'editing',
        workspaceId: primaryWorkspaceId,
        filePath: 'src/demo_output.py',
        message: 'Modified demo_output.py (simulated)',
        delayMs: 700,
      },
      {
        eventType: 'participant.activity_update',
        stage: 'testing',
        workspaceId: primaryWorkspaceId,
        message: 'Continuing authorised work',
        delayMs: 800,
      },

      // 11. Audisor records its own structured outcome
      {
        eventType: 'participant.outcome_recorded',
        stage: 'completed',
        workspaceId: primaryWorkspaceId,
        message: 'Audisor work completed',
        delayMs: 500,
        metadata: { entry: audisorFinalEntry },
      },

      // 12. Task completes
      {
        eventType: 'task.completed',
        stage: 'completed',
        workspaceId: primaryWorkspaceId,
        message: 'Task completed successfully',
        delayMs: 400,
      },
    ]

    return steps
  }
}
