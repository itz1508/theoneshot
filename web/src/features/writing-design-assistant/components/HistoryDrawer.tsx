/**
 * HistoryDrawer — session history with explicit clear.
 * Entries live only in memory (typed HistoryStore); nothing persists
 * beyond the page and nothing is ever fed back into prompts.
 */

import { Trash2, X } from 'lucide-react'
import { modeDefinition } from '../modes'
import type { HistoryEntry } from '../types'
import styles from '../WritingDesignAssistant.module.css'

interface HistoryDrawerProps {
  open: boolean
  entries: HistoryEntry[]
  onClose: () => void
  onClear: () => void
  onSelect: (entry: HistoryEntry) => void
}

export function HistoryDrawer({ open, entries, onClose, onClear, onSelect }: HistoryDrawerProps) {
  if (!open) return null
  return (
    <aside className={styles.historyDrawer} aria-label="Request history">
      <header className={styles.historyHeader}>
        <h3>History</h3>
        <div className={styles.historyActions}>
          <button
            type="button"
            className={styles.historyClear}
            onClick={onClear}
            disabled={entries.length === 0}
            aria-label="Clear history"
          >
            <Trash2 size={14} aria-hidden="true" />
            Clear
          </button>
          <button
            type="button"
            className={styles.historyClose}
            onClick={onClose}
            aria-label="Close history"
          >
            <X size={14} aria-hidden="true" />
          </button>
        </div>
      </header>
      <p className={styles.historyNote}>Kept in memory for this session only.</p>
      {entries.length === 0 ? (
        <p className={styles.historyEmpty}>No requests yet.</p>
      ) : (
        <ul className={styles.historyList}>
          {entries.map((entry) => (
            <li key={entry.request_id}>
              <button
                type="button"
                className={styles.historyEntry}
                onClick={() => onSelect(entry)}
              >
                <span className={styles.historyMode}>{modeDefinition(entry.mode).label}</span>
                <span className={styles.historyStatus} data-status={entry.status}>
                  {entry.status}
                </span>
                <span className={styles.historyPreview}>{entry.input_preview}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </aside>
  )
}
