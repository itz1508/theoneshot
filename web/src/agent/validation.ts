/**
 * Runtime validation for TaskRecordEntry payloads.
 *
 * Enforces the discriminated-union contract at the event-admission boundary.
 * Rejects malformed payloads that TypeScript alone cannot guard against
 * when data arrives from external event sources.
 */

import type { TaskRecordEntry } from './types'

export interface ValidationFailure {
  valid: false
  reason: string
}

export interface ValidationSuccess {
  valid: true
  entry: TaskRecordEntry
}

export type ValidationResult = ValidationSuccess | ValidationFailure

export function validateRecordEntry(payload: unknown): ValidationResult {
  if (!payload || typeof payload !== 'object') {
    return { valid: false, reason: 'Record entry payload is not an object' }
  }

  const p = payload as Record<string, unknown>

  // Required base fields
  const requiredBase = ['entryId', 'activityId', 'participantId', 'title', 'outcome', 'status', 'timestamp']
  for (const field of requiredBase) {
    if (p[field] === undefined || p[field] === null) {
      return { valid: false, reason: `Missing required field: ${field}` }
    }
  }

  const status = p.status as string

  // Status-specific validation
  if (status === 'correction_required' || status === 'failed') {
    // rootCause is mandatory
    if (!p.rootCause || typeof p.rootCause !== 'object') {
      return { valid: false, reason: `${status} entry requires rootCause` }
    }
    const rc = p.rootCause as Record<string, unknown>
    if (!rc.summary || typeof rc.summary !== 'string') {
      return { valid: false, reason: `${status} entry requires rootCause.summary` }
    }
    if (!Array.isArray(rc.evidence)) {
      return { valid: false, reason: `${status} entry requires rootCause.evidence array` }
    }

    // resolution is mandatory
    if (!p.resolution || typeof p.resolution !== 'object') {
      return { valid: false, reason: `${status} entry requires resolution` }
    }
    const res = p.resolution as Record<string, unknown>
    if (!res.summary || typeof res.summary !== 'string') {
      return { valid: false, reason: `${status} entry requires resolution.summary` }
    }
    if (!res.action || typeof res.action !== 'string') {
      return { valid: false, reason: `${status} entry requires resolution.action` }
    }
    if (!res.status || !['proposed', 'applied', 'verified'].includes(res.status as string)) {
      return { valid: false, reason: `${status} entry requires resolution.status (proposed|applied|verified)` }
    }
    if (!Array.isArray(res.evidence)) {
      return { valid: false, reason: `${status} entry requires resolution.evidence array` }
    }

    // applied resolution must have supporting evidence
    if (res.status === 'applied' && (res.evidence as unknown[]).length === 0) {
      return { valid: false, reason: `resolution.status=applied requires supporting evidence` }
    }
    // verified resolution must have verification evidence
    if (res.status === 'verified' && (res.evidence as unknown[]).length === 0) {
      return { valid: false, reason: `resolution.status=verified requires verification evidence` }
    }
  }

  if (status === 'blocked') {
    if (!p.blockingReason || typeof p.blockingReason !== 'string') {
      return { valid: false, reason: `blocked entry requires blockingReason` }
    }
  }

  if (status === 'cancelled') {
    if (!p.cancellationReason || typeof p.cancellationReason !== 'string') {
      return { valid: false, reason: `cancelled entry requires cancellationReason` }
    }
  }

  if (!['completed', 'correction_required', 'failed', 'blocked', 'cancelled'].includes(status)) {
    return { valid: false, reason: `Unknown entry status: ${status}` }
  }

  return { valid: true, entry: payload as TaskRecordEntry }
}
