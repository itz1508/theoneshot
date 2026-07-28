/**
 * ReplyOptions — the three reply variants as copyable cards,
 * plus the one-line "in short" reading of the message.
 */

import type { DraftThreeRepliesResult } from '../types'
import { CopyButton } from './AssistantResult'
import styles from '../WritingDesignAssistant.module.css'

interface ReplyOptionsProps {
  result: DraftThreeRepliesResult
}

const VARIANTS: { key: 'brief' | 'thorough' | 'diplomatic'; label: string }[] = [
  { key: 'brief', label: 'Brief' },
  { key: 'thorough', label: 'Thorough' },
  { key: 'diplomatic', label: 'Diplomatic' },
]

export function ReplyOptions({ result }: ReplyOptionsProps) {
  return (
    <div className={styles.replyOptions}>
      <p className={styles.inShort}>
        <strong>In short:</strong> {result.in_short}
      </p>
      <p className={styles.replyMeta}>
        Purpose: {result.message_purpose} · Tone: {result.tone}
      </p>
      <div className={styles.replyCards}>
        {VARIANTS.map(({ key, label }) => (
          <article key={key} className={styles.replyCard} aria-label={`${label} reply`}>
            <header className={styles.replyCardHeader}>
              <h4>{label}</h4>
              <CopyButton text={result[key]} label={`Copy ${label.toLowerCase()} reply`} />
            </header>
            <p className={styles.replyText}>{result[key]}</p>
          </article>
        ))}
      </div>
    </div>
  )
}
