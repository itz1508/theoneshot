import type { MessageTokenUsage } from '../agent/types'
import styles from './TokenBadge.module.css'

export function TokenBadge({ tokens }: { tokens: MessageTokenUsage }) {
  const costLabel =
    tokens.provider === 'local'
      ? 'local model'
      : tokens.cost != null
        ? `$${tokens.cost.toFixed(6)}`
        : 'cost n/a'

  return (
    <span className={styles.badge} aria-label="Token usage">
      <span className={styles.stat}>
        <span className={styles.dim}>in</span> {tokens.input_tokens.toLocaleString()}
      </span>
      <span className={styles.sep}>·</span>
      <span className={styles.stat}>
        <span className={styles.dim}>out</span> {tokens.output_tokens.toLocaleString()}
      </span>
      <span className={styles.sep}>·</span>
      <span className={`${styles.cost} ${tokens.provider === 'local' ? styles.local : ''}`}>
        {costLabel}
      </span>
    </span>
  )
}
