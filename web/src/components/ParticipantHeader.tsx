/**
 * ParticipantHeader — compact active-participant display with 3D flip transition.
 * Shows: participant name, status indicator, status label, activity summary.
 * Flip driven by activeParticipantId state change, not animation timer.
 * prefers-reduced-motion: cross-fade instead of 3D flip.
 */

import { useRef, useEffect, useState } from 'react'
import type { ParticipantId, ActivityStatus } from '../agent/types'
import styles from './ParticipantHeader.module.css'

interface ParticipantHeaderProps {
  participantId: ParticipantId | null
  status: ActivityStatus
  summary: string
}

const participantLabels: Record<string, string> = {
  audisor: 'Audisor',
  aflow: 'A-Flow',
}

const statusLabels: Record<ActivityStatus, string> = {
  idle: 'Idle',
  working: 'Working',
  waiting: 'Waiting',
  blocked: 'Blocked',
  completed: 'Completed',
  failed: 'Failed',
  cancelled: 'Cancelled',
}

export function ParticipantHeader({ participantId, status, summary }: ParticipantHeaderProps) {
  const prevRef = useRef<ParticipantId | null>(null)
  const [flipping, setFlipping] = useState(false)
  const [displayParticipant, setDisplayParticipant] = useState(participantId)

  useEffect(() => {
    if (prevRef.current !== null && participantId !== null && prevRef.current !== participantId) {
      setFlipping(true)
      const timer = setTimeout(() => {
        setDisplayParticipant(participantId)
        setFlipping(false)
      }, 300)
      prevRef.current = participantId
      return () => clearTimeout(timer)
    }
    prevRef.current = participantId
    setDisplayParticipant(participantId)
  }, [participantId])

  if (!displayParticipant) {
    return (
      <div className={styles.header}>
        <div className={styles.empty}>No active participant</div>
      </div>
    )
  }

  const label = participantLabels[displayParticipant] ?? displayParticipant

  return (
    <div className={styles.header}>
      <div className={`${styles.card} ${flipping ? styles.flipping : ''}`}>
        <div className={styles.face}>
          <span className={styles.name}>{label}</span>
          <span className={`${styles.indicator} ${styles[`indicator_${status}`]}`} />
          <span className={styles.status}>{statusLabels[status]}</span>
        </div>
        <p className={styles.summary}>{summary}</p>
      </div>
    </div>
  )
}
