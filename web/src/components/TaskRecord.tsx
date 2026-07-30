/**
 * TaskRecord — persistent section showing structured recorded outcomes.
 * Only shows recorded entries; never live activity updates or pending placeholders.
 * Retries create new entries; history is never rewritten.
 *
 * For Correction needed and Failed entries, renders expanded:
 *   Title + status → Result → Root cause → Resolution → Resolution state → Evidence → Next authorised action
 */

import type { TaskRecordEntry } from '../agent/types'
import { RootCauseResolutionDetails } from './RootCauseResolutionDetails'
import styles from './TaskRecord.module.css'

interface TaskRecordProps {
  entries: TaskRecordEntry[]
}

const statusIcons: Record<TaskRecordEntry['status'], string> = {
  completed: '\u2713',
  correction_required: '!',
  blocked: '\u2717',
  failed: '\u2717',
  cancelled: '\u2014',
}

const statusLabels: Record<TaskRecordEntry['status'], string> = {
  completed: 'Completed',
  correction_required: 'Correction needed',
  blocked: 'Blocked',
  failed: 'Failed',
  cancelled: 'Cancelled',
}

const statusClasses: Record<TaskRecordEntry['status'], string> = {
  completed: 'statusCompleted',
  correction_required: 'statusCorrection',
  blocked: 'statusBlocked',
  failed: 'statusFailed',
  cancelled: 'statusCancelled',
}

export function TaskRecord({ entries }: TaskRecordProps) {
  if (entries.length === 0) return null

  return (
    <div className={styles.container}>
      <div className={styles.heading}>Task Record</div>
      <div className={styles.list}>
        {entries.map((entry) => (
          <div key={entry.entryId} className={styles.entry}>
            {/* Title + status */}
            <div className={styles.entryHeader}>
              <span className={`${styles.icon} ${styles[statusClasses[entry.status]]}`}>
                {statusIcons[entry.status]}
              </span>
              <span className={styles.title}>{entry.title}</span>
              <span className={styles.statusBadge}>{statusLabels[entry.status]}</span>
            </div>
            <div className={styles.details}>
              {/* Result */}
              <div className={styles.detail}>
                <span className={styles.detailLabel}>Result:</span>
                <span className={styles.detailValue}>{entry.outcome}</span>
              </div>

              {/* Participant */}
              <div className={styles.detail}>
                <span className={styles.detailLabel}>Participant:</span>
                <span className={styles.detailValue}>
                  {entry.participantId === 'audisor' ? 'Audisor' : entry.participantId === 'aflow' ? 'A-Flow' : entry.participantId}
                </span>
              </div>

              {/* Root cause — expanded, for correction_required and failed */}
              {(entry.status === 'correction_required' || entry.status === 'failed') && (
                <RootCauseResolutionDetails
                  kind="cause"
                  directCause={entry.rootCause.summary}
                  directLabel="Root cause"
                />
              )}

              {/* Resolution — expanded, for correction_required and failed */}
              {(entry.status === 'correction_required' || entry.status === 'failed') && (
                <RootCauseResolutionDetails
                  kind="resolution"
                  resolution={entry.resolution.summary}
                  resolutionState={entry.resolution.status}
                />
              )}

              {/* Blocking reason — for blocked */}
              {entry.status === 'blocked' && (
                <div className={styles.detail}>
                  <span className={styles.detailLabel}>Blocking reason:</span>
                  <span className={styles.detailValue}>{entry.blockingReason}</span>
                </div>
              )}

              {/* Cancellation reason — for cancelled */}
              {entry.status === 'cancelled' && (
                <div className={styles.detail}>
                  <span className={styles.detailLabel}>Cancellation reason:</span>
                  <span className={styles.detailValue}>{entry.cancellationReason}</span>
                </div>
              )}

              {/* Material gaps */}
              {entry.materialGaps.length > 0 && (
                <div className={styles.detail}>
                  <span className={styles.detailLabel}>Material gaps:</span>
                  <span className={styles.detailValue}>{entry.materialGaps.length}</span>
                </div>
              )}

              {/* Evidence */}
              {entry.evidence.length > 0 && (
                <div className={styles.detail}>
                  <span className={styles.detailLabel}>Evidence:</span>
                  <span className={styles.detailValue}>{entry.evidence.join(', ')}</span>
                </div>
              )}

              {/* Next authorised action */}
              {entry.nextAuthorisedAction && (
                <div className={styles.detail}>
                  <span className={styles.detailLabel}>Next authorised action:</span>
                  <span className={styles.detailValue}>{entry.nextAuthorisedAction}</span>
                </div>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
