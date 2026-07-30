import styles from './TaskRecord.module.css'

interface CauseProps {
  kind: 'cause'
  directCause: string
  underlyingCause?: string
  directLabel?: string
}

interface ResolutionProps {
  kind: 'resolution'
  resolution: string | string[]
  resolutionState?: string
}

export function RootCauseResolutionDetails(props: CauseProps | ResolutionProps) {
  if (props.kind === 'cause') {
    return (
      <>
        <div className={styles.detail}>
          <span className={styles.detailLabel}>{props.directLabel ?? 'Direct cause'}:</span>
          <span className={styles.detailValue}>{props.directCause}</span>
        </div>
        {props.underlyingCause ? (
          <div className={styles.detail}>
            <span className={styles.detailLabel}>Underlying cause:</span>
            <span className={styles.detailValue}>{props.underlyingCause}</span>
          </div>
        ) : null}
      </>
    )
  }
  const text = Array.isArray(props.resolution) ? props.resolution.join(' ') : props.resolution
  return (
    <>
      <div className={styles.detail}>
        <span className={styles.detailLabel}>Resolution:</span>
        <span className={styles.detailValue}>{text}</span>
      </div>
      {props.resolutionState ? (
        <div className={styles.detail}>
          <span className={styles.detailLabel}>Resolution state:</span>
          <span className={styles.detailValue}>{props.resolutionState}</span>
        </div>
      ) : null}
    </>
  )
}
