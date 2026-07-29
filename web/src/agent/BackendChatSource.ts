/**
 * BackendChatSource — real backend implementation of TaskEventSource.
 *
 * Sends user messages to POST /v1/chat and emits agent events
 * with real provider-reported token usage. Never falls back to
 * demo/mock/canned content.
 */

import type { AgentEvent, EventType, Stage } from './types'
import type { TaskEventSource, EventListener, StartOptions } from './TaskEventSource'

let nextId = 0
function uid(): string {
  return `evt-${Date.now()}-${nextId++}`
}

export class BackendChatSource implements TaskEventSource {
  private listeners = new Set<EventListener>()
  private abortController: AbortController | null = null
  private currentTaskId: string | null = null

  start(
    message: string,
    _primaryWorkspaceId: string,
    _linkedWorkspaceIds: string[],
    options?: StartOptions,
  ): string {
    // Cancel any in-flight request
    this.abortController?.abort()

    const taskId = `task-${Date.now()}`
    this.currentTaskId = taskId
    this.abortController = new AbortController()

    // Fire async — start() returns taskId synchronously per interface contract
    this._dispatch(taskId, message, this.abortController.signal, options)

    return taskId
  }

  subscribe(listener: EventListener): () => void {
    this.listeners.add(listener)
    return () => { this.listeners.delete(listener) }
  }

  cancel(): void {
    if (!this.abortController) return
    this.abortController.abort()
    this.abortController = null

    if (this.currentTaskId) {
      this.emit({
        eventId: uid(),
        sequence: 999,
        taskId: this.currentTaskId,
        eventType: 'task.cancelled',
        stage: 'cancelled',
        message: 'Task cancelled by operator',
        timestamp: new Date().toISOString(),
      })
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

  private async _dispatch(
    taskId: string,
    message: string,
    signal: AbortSignal,
    options?: StartOptions,
  ): Promise<void> {
    let seq = 0

    // Emit task.created
    this.emit({
      eventId: uid(),
      sequence: seq++,
      taskId,
      eventType: 'task.created',
      stage: 'queued',
      message: 'Sending to backend...',
      timestamp: new Date().toISOString(),
    })

    // Emit participant.activated (agent working)
    this.emit({
      eventId: uid(),
      sequence: seq++,
      taskId,
      eventType: 'participant.activated',
      stage: 'reading',
      message: 'Processing request',
      timestamp: new Date().toISOString(),
      metadata: { participantId: 'audisor', activityId: `act-${Date.now()}` },
    })

    try {
      const response = await fetch('/v1/chat', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-Audisor-Dev-User': 'operator',
        },
        body: JSON.stringify({
          message,
          ...(options?.history?.length ? { history: options.history } : {}),
          ...(options?.model ? { model: options.model } : {}),
        }),
        signal,
      })

      if (!response.ok) {
        let errorMsg = `Backend error: ${response.status}`
        try {
          const errBody = await response.json()
          if (errBody.error) errorMsg = errBody.error
        } catch { /* use status code */ }

        this.emit({
          eventId: uid(),
          sequence: seq++,
          taskId,
          eventType: 'task.failed' as EventType,
          stage: 'cancelled' as Stage,
          message: errorMsg,
          timestamp: new Date().toISOString(),
        })
        return
      }

      const data = await response.json()

      // Emit task.completed with real token metadata
      this.emit({
        eventId: uid(),
        sequence: seq++,
        taskId,
        eventType: 'task.completed',
        stage: 'completed',
        message: data.reply,
        timestamp: new Date().toISOString(),
        metadata: {
          inputTokens: data.usage.input_tokens,
          outputTokens: data.usage.output_tokens,
          totalTokens: data.usage.total_tokens,
          cost: data.usage.cost,
          tokenProvider: data.usage.provider_type,
          model: data.model,
          providerId: data.provider.id,
        },
      })
    } catch (err: unknown) {
      if (signal.aborted) return // User cancelled, already handled

      const errorMessage = err instanceof Error
        ? `Backend unavailable: ${err.message}`
        : 'Backend unavailable: unknown error'

      this.emit({
        eventId: uid(),
        sequence: seq++,
        taskId,
        eventType: 'task.failed' as EventType,
        stage: 'cancelled' as Stage,
        message: errorMessage,
        timestamp: new Date().toISOString(),
      })
    }
  }
}
