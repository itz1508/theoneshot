/**
 * ToolApprovalDialog — confirmation UI for operator approval of tool calls.
 *
 * Shown when the backend returns HTTP 202 ChatApprovalRequired, indicating
 * a write/exec tool needs explicit operator confirmation before execution.
 */

import { useState } from 'react'
import type { ToolCallEvent } from '../agent/types'
import styles from './ToolApprovalDialog.module.css'

export interface ToolApprovalDialogProps {
  toolCall: ToolCallEvent
  reason: string
  riskLevel: 'low' | 'medium' | 'high'
  onResolve: (approved: boolean) => void
}

const riskLabels: Record<string, string> = {
  low: 'Low risk',
  medium: 'Medium risk',
  high: 'High risk',
}

const riskEmoji: Record<string, string> = {
  low: '\u2139\uFE0F',
  medium: '\u26A0\uFE0F',
  high: '\uD83D\uDED1',
}

export function ToolApprovalDialog({
  toolCall,
  reason,
  riskLevel,
  onResolve,
}: ToolApprovalDialogProps) {
  const [resolved, setResolved] = useState(false)

  const handleApprove = () => {
    if (resolved) return
    setResolved(true)
    onResolve(true)
  }

  const handleDeny = () => {
    if (resolved) return
    setResolved(true)
    onResolve(false)
  }

  const argsPreview = JSON.stringify(toolCall.arguments, null, 2).slice(0, 200)

  return (
    <div className={styles.overlay}>
      <div className={`${styles.dialog} ${styles[riskLevel]}`}>
        <div className={styles.header}>
          <span className={styles.icon}>{riskEmoji[riskLevel]}</span>
          <span className={styles.title}>Tool Approval Required</span>
          <span className={styles.risk}>{riskLabels[riskLevel]}</span>
        </div>

        <div className={styles.body}>
          <div className={styles.field}>
            <span className={styles.label}>Tool</span>
            <code className={styles.value}>{toolCall.tool_name}</code>
          </div>

          <div className={styles.field}>
            <span className={styles.label}>Reason</span>
            <p className={styles.value}>{reason}</p>
          </div>

          <div className={styles.field}>
            <span className={styles.label}>Arguments</span>
            <pre className={styles.args}>{argsPreview}</pre>
          </div>
        </div>

        <div className={styles.actions}>
          <button
            className={styles.denyBtn}
            onClick={handleDeny}
            disabled={resolved}
          >
            Deny
          </button>
          <button
            className={styles.approveBtn}
            onClick={handleApprove}
            disabled={resolved}
          >
            Approve
          </button>
        </div>
      </div>
    </div>
  )
}
