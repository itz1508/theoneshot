import styles from './UserMessage.module.css'

interface UserMessageProps {
  content: string
}

export function UserMessage({ content }: UserMessageProps) {
  return (
    <div className={styles.wrapper}>
      <div className={styles.column}>
        <span className={styles.label}>You</span>
        <div className={styles.bubble}>
          <p className={styles.text}>{content}</p>
        </div>
      </div>
    </div>
  )
}
