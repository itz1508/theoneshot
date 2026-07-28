/**
 * ChangeList — per-correction breakdown for fix_wording results.
 * Possibly-intentional phrasing is marked, never silently changed.
 */

import type { FixWordingChange } from '../types'
import styles from '../WritingDesignAssistant.module.css'

interface ChangeListProps {
  changes: FixWordingChange[]
  noChangesNeeded: boolean
}

export function ChangeList({ changes, noChangesNeeded }: ChangeListProps) {
  if (noChangesNeeded || changes.length === 0) {
    return <p className={styles.noChanges}>No corrections needed — your wording is fine.</p>
  }
  return (
    <ul className={styles.changeList} aria-label="Corrections">
      {changes.map((change, index) => (
        <li key={`${change.original}-${index}`} className={styles.changeItem}>
          <span className={styles.changeOriginal}>{change.original}</span>
          <span aria-hidden="true" className={styles.changeArrow}>
            →
          </span>
          <span className={styles.changeCorrection}>{change.correction}</span>
          <p className={styles.changeReason}>{change.reason}</p>
          {change.intentional_possible ? (
            <p className={styles.changeIntentional}>
              This may be intentional phrasing — kept your call.
            </p>
          ) : null}
        </li>
      ))}
    </ul>
  )
}
