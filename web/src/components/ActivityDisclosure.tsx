import styles from './ActivityDisclosure.module.css'

interface ActivityDisclosureProps {
  label: string
  detail: string
  status: 'completed' | 'running' | 'pending'
}

const statusLabels: Record<string, string> = {
  completed: 'done',
  running: 'running',
  pending: 'pending',
}

export function ActivityDisclosure({ label, detail, status }: ActivityDisclosureProps) {
  return (
    <div className={styles.item}>
      <div className={`${styles.indicator} ${styles[status]}`} />
      <div className={styles.body}>
        <div className={styles.header}>
          <span className={styles.label}>{label}</span>
          <span className={styles.statusLabel}>{statusLabels[status]}</span>
        </div>
        <p className={styles.detail}>{detail}</p>
      </div>
    </div>
  )
}
