/**
 * TaskEventSource — the single interface consumed by the UI store.
 *
 * Implementations:
 *   - BackendChatSource (real backend via POST /v1/chat)
 *
 * No UI component may import a concrete implementation directly.
 * Only the application bootstrap (App.tsx) instantiates and binds one.
 */

import type { AgentEvent } from './types'
import type { ChatHistoryEntry } from './chatCapacity'

export type EventListener = (event: AgentEvent) => void

/** Optional request context sent along with the next chat turn. */
export interface StartOptions {
  /** Prior conversation turns to send to the backend. */
  history?: ChatHistoryEntry[]
  /** Model override within the configured provider. */
  model?: string | null
}

export interface TaskEventSource {
  /** Start emitting events for a task. Returns a task ID. */
  start(
    message: string,
    primaryWorkspaceId: string,
    linkedWorkspaceIds: string[],
    options?: StartOptions,
  ): string
  /** Subscribe to events. Returns an unsubscribe function. */
  subscribe(listener: EventListener): () => void
  /** Cancel the current task. */
  cancel(): void
  /** Clean up resources. */
  dispose(): void
}
