/**
 * TaskReviewDrawer — 320px right-side panel.
 * Single-active-participant surface:
 *   - ParticipantHeader (compact active participant with 3D flip)
 *   - LiveActivity (scrolling activity updates)
 *   - TaskRecord (persistent recorded outcomes)
 * No tabs, no lifecycle strip, no predetermined stages.
 */

import { X, PanelRightOpen } from 'lucide-react'
import type { TaskState } from '../agent/types'
import { ParticipantHeader } from './ParticipantHeader'
import { LiveActivity } from './LiveActivity'
import { TaskRecord } from './TaskRecord'
import styles from './TaskReviewDrawer.module.css'

interface TaskReviewDrawerProps {
  open: boolean
  task: TaskState
  runnerMode: string
  onToggle: () => void
  onCancel: () => void
}

export function TaskReviewDrawer({ open, task, runnerMode, onToggle, onCancel }: TaskReviewDrawerProps) {
  const hasTask = task.taskId !== null
  const isRunning = task.status === 'running' || task.status === 'queued'

  if (!open) {
    return (
      <div className={styles.collapsed}>
        <button className={styles.expandBtn} onClick={onToggle} aria-label="Open Task Review">
          <PanelRightOpen size={16} />
        </button>
      </div>
    )
  }

  return (
    <div className={styles.drawer} style={{ width: open ? 320 : 0 }}>
      <div className={styles.inner}>
        <div className={styles.header}>
          <span className={styles.title}>Task Review</span>
          <button className={styles.closeBtn} onClick={onToggle} aria-label="Close drawer">
            <X size={14} />
          </button>
        </div>

        {hasTask ? (
          <div className={styles.body}>
            {/* Objective */}
            <div className={styles.objective}>
              <span className={styles.objectiveLabel}>Objective</span>
              <p className={styles.objectiveText}>{task.objective}</p>
              <span className={styles.runnerBadge}>{runnerMode}</span>
            </div>

            {/* Active participant header with flip */}
            <ParticipantHeader
              participantId={task.activeParticipantId}
              status={task.activeActivity?.status ?? 'idle'}
              summary={task.activeActivity?.summary ?? ''}
            />

            {/* Live Activity */}
            <LiveActivity
              messages={task.activeActivity?.messages ?? []}
            />

            {/* Task Record */}
            <TaskRecord entries={task.taskRecord} />

            {/* Cancel */}
            {isRunning && (
              <div className={styles.cancelSection}>
                <button className={styles.cancelBtn} onClick={onCancel}>
                  Cancel task
                </button>
              </div>
            )}
          </div>
        ) : (
          <div className={styles.empty}>
            <p className={styles.emptyText}>No active task</p>
            <p className={styles.emptyHint}>Send a message to start a task</p>
          </div>
        )}
      </div>
    </div>
  )
}
