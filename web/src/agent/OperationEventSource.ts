/** Optional reattachment capability implemented by operation-backed sources. */
import type { TaskEventSource } from './TaskEventSource'

export interface OperationEventSource extends TaskEventSource {
  reattach(operationId: string): void
}
