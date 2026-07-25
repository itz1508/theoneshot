import styles from './AgentLoadingState.module.css'

interface AgentLoadingStateProps {
  text?: string
}

export function AgentLoadingState({ text = 'Audisor is reviewing the task…' }: AgentLoadingStateProps) {
  return (
    <div className={styles.wrapper}>
      <div className={styles.indicator}>
        <div className={styles.dots}>
          <span className={styles.dot} />
          <span className={styles.dot} />
          <span className={styles.dot} />
        </div>
        <span className={styles.text}>{text}</span>
      </div>
    </div>
  )
}
