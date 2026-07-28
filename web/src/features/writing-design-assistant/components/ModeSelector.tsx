/**
 * ModeSelector — six assistant modes as an accessible toggle group.
 */

import { MODES } from '../modes'
import type { AssistantMode } from '../types'
import styles from '../WritingDesignAssistant.module.css'

interface ModeSelectorProps {
  active: AssistantMode
  disabled?: boolean
  onSelect: (mode: AssistantMode) => void
}

export function ModeSelector({ active, disabled, onSelect }: ModeSelectorProps) {
  return (
    <div className={styles.modeGrid} role="group" aria-label="Assistant mode">
      {MODES.map((mode) => {
        const Icon = mode.icon
        const isActive = mode.id === active
        return (
          <button
            key={mode.id}
            type="button"
            className={`${styles.modeCard} ${isActive ? styles.modeCardActive : ''}`}
            aria-pressed={isActive}
            disabled={disabled}
            onClick={() => onSelect(mode.id)}
          >
            <Icon size={18} strokeWidth={1.6} aria-hidden="true" />
            <span className={styles.modeLabel}>{mode.label}</span>
            <span className={styles.modeDescription}>{mode.description}</span>
          </button>
        )
      })}
    </div>
  )
}
