/**
 * ActivityLED — 5 visual states for workspace activity.
 * Always accompanied by a readable stage label.
 * Respects prefers-reduced-motion.
 */

import { Circle, CircleDot, CirclePause, CircleAlert, CircleCheck } from 'lucide-react'
import { stageToLed, stageLabel } from '../agent/types'
import type { Stage, LedState } from '../agent/types'
import styles from './ActivityLED.module.css'

interface ActivityLEDProps {
  stage: Stage
  onClick?: () => void
}

/** Static icon per LED state (for reduced-motion or always) */
const ledIcons: Record<LedState, typeof Circle> = {
  idle: Circle,
  working: CircleDot,
  waiting: CirclePause,
  blocked: CircleAlert,
  completed: CircleCheck,
}

export function ActivityLED({ stage, onClick }: ActivityLEDProps) {
  const led = stageToLed(stage)
  const label = stageLabel(stage)
  const Icon = ledIcons[led]

  return (
    <button
      className={`${styles.led} ${styles[led]}`}
      onClick={onClick}
      aria-label={`${label} — click to view task details`}
      title={label}
    >
      <span className={styles.dot} aria-hidden="true">
        <Icon size={10} />
      </span>
    </button>
  )
}
