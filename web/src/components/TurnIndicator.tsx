/**
 * TurnIndicator — persistent badge showing whose turn it is.
 * Sits above the composer and updates reactively with the turn state.
 */

import styles from './TurnIndicator.module.css'

interface TurnIndicatorProps {
  turn: 'user' | 'agent'
}

export function TurnIndicator({ turn }: TurnIndicatorProps) {
  return (
    <div className={styles.row}>
      <span
        className={`${styles.badge} ${turn === 'user' ? styles.userTurn : styles.agentTurn}`}
        role="status"
        aria-live="polite"
      >
        {turn === 'user' ? (
          <>
            <span className={styles.dot} />
            <span>Your turn</span>
          </>
        ) : (
          <>
            <span className={styles.dots}>
              <span className={styles.typingDot} />
              <span className={styles.typingDot} />
              <span className={styles.typingDot} />
            </span>
            <span>Assistant is typing…</span>
          </>
        )}
      </span>
    </div>
  )
}
