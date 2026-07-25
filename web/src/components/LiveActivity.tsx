/**
 * LiveActivity — scrolling activity-update stream.
 * Shows concise activity updates for the current participant's active activity.
 * Auto-scrolls to latest. Clears after outcome is recorded.
 */

import { useRef, useEffect } from 'react'
import type { ActivityUpdate } from '../agent/types'
import styles from './LiveActivity.module.css'

interface LiveActivityProps {
  messages: ActivityUpdate[]
}

export function LiveActivity({ messages }: LiveActivityProps) {
  const scrollRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [messages])

  if (messages.length === 0) {
    return null
  }

  return (
    <div className={styles.container} ref={scrollRef}>
      {messages.map((msg) => (
        <div key={msg.id} className={styles.entry}>
          <span className={styles.dot} aria-hidden="true" />
          <span className={styles.text}>{msg.text}</span>
        </div>
      ))}
    </div>
  )
}
