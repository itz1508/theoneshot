/**
 * TaskEventSource — the single interface consumed by the UI store.
 *
 * Implementations:
 *   - DemoTaskEventSource (deterministic local mock, this increment)
 *   - [future] SseTaskEventSource (real backend SSE stream)
 *
 * No UI component may import a concrete implementation directly.
 * Only the application bootstrap (App.tsx) instantiates and binds one.
 */

import type { AgentEvent } from './types'

export type EventListener = (event: AgentEvent) => void

export interface TaskEventSource {
  /** Start emitting events for a task. Returns a task ID. */
  start(message: string, primaryWorkspaceId: string, linkedWorkspaceIds: string[]): string
  /** Subscribe to events. Returns an unsubscribe function. */
  subscribe(listener: EventListener): () => void
  /** Cancel the current task. */
  cancel(): void
  /** Clean up resources. */
  dispose(): void
}
