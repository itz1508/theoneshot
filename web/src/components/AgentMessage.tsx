import { useState } from 'react'
import { ActivityDisclosure } from './ActivityDisclosure'
import styles from './AgentMessage.module.css'

interface Activity {
  id: string
  label: string
  detail: string
  status: 'completed' | 'running' | 'pending'
}

interface AgentMessageProps {
  content: string
  activities?: Activity[]
}

export function AgentMessage({ content, activities }: AgentMessageProps) {
  const [expanded, setExpanded] = useState(false)

  return (
    <div className={styles.wrapper}>
      <div className={styles.column}>
        <span className={styles.label}>Assistant</span>
        <div className={styles.bubble}>
          <p className={styles.text}>{content}</p>
          {activities && activities.length > 0 && (
            <div className={styles.activitySection}>
              <button
                className={styles.activityToggle}
                onClick={() => setExpanded((prev) => !prev)}
              >
                <svg
                  width="12"
                  height="12"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  className={`${styles.chevron} ${expanded ? styles.chevronOpen : ''}`}
                >
                  <polyline points="6 9 12 15 18 9" />
                </svg>
                <span>{expanded ? 'Hide activities' : 'Show activities'}</span>
                <span className={styles.activityCount}>{activities.length}</span>
              </button>
              {expanded && (
                <div className={styles.activityList}>
                  {activities.map((activity) => (
                    <ActivityDisclosure
                      key={activity.id}
                      label={activity.label}
                      detail={activity.detail}
                      status={activity.status}
                    />
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
