/**
 * AssistantInput — text, optional selected term, context, and tone.
 * Submits via button or Ctrl/Cmd+Enter; disabled while a request runs.
 */

import { useId, type KeyboardEvent } from 'react'
import { SendHorizontal } from 'lucide-react'
import { modeDefinition } from '../modes'
import type { AssistantMode } from '../types'
import styles from '../WritingDesignAssistant.module.css'

export interface AssistantInputValue {
  text: string
  selectedText: string
  context: string
  tone: string
}

interface AssistantInputProps {
  mode: AssistantMode
  value: AssistantInputValue
  loading: boolean
  onChange: (value: AssistantInputValue) => void
  onSubmit: () => void
}

export function AssistantInput({ mode, value, loading, onChange, onSubmit }: AssistantInputProps) {
  const textId = useId()
  const selectedId = useId()
  const contextId = useId()
  const toneId = useId()
  const definition = modeDefinition(mode)
  const canSubmit = value.text.trim().length > 0 && !loading

  const handleKeyDown = (event: KeyboardEvent) => {
    if ((event.ctrlKey || event.metaKey) && event.key === 'Enter' && canSubmit) {
      event.preventDefault()
      onSubmit()
    }
  }

  return (
    <div className={styles.inputArea}>
      <label className={styles.fieldLabel} htmlFor={textId}>
        Your text
      </label>
      <textarea
        id={textId}
        className={styles.textInput}
        value={value.text}
        placeholder="Paste or type the text you want help with…"
        rows={6}
        disabled={loading}
        onChange={(event) => onChange({ ...value, text: event.target.value })}
        onKeyDown={handleKeyDown}
      />

      <div className={styles.fieldRow}>
        <div className={styles.field}>
          <label className={styles.fieldLabel} htmlFor={selectedId}>
            Selected term{definition.requiresSelectedText ? ' (required)' : ''}
          </label>
          <input
            id={selectedId}
            className={styles.smallInput}
            value={value.selectedText}
            placeholder={definition.requiresSelectedText ? 'Term to translate' : 'Optional'}
            disabled={loading}
            onChange={(event) => onChange({ ...value, selectedText: event.target.value })}
            onKeyDown={handleKeyDown}
          />
        </div>
        <div className={styles.field}>
          <label className={styles.fieldLabel} htmlFor={contextId}>
            Context
          </label>
          <input
            id={contextId}
            className={styles.smallInput}
            value={value.context}
            placeholder="Optional"
            disabled={loading}
            onChange={(event) => onChange({ ...value, context: event.target.value })}
            onKeyDown={handleKeyDown}
          />
        </div>
        <div className={styles.field}>
          <label className={styles.fieldLabel} htmlFor={toneId}>
            Tone
          </label>
          <input
            id={toneId}
            className={styles.smallInput}
            value={value.tone}
            placeholder="Optional"
            disabled={loading}
            onChange={(event) => onChange({ ...value, tone: event.target.value })}
            onKeyDown={handleKeyDown}
          />
        </div>
      </div>

      <button
        type="button"
        className={styles.submitButton}
        aria-label={loading ? 'Generating' : 'Send'}
        title={loading ? 'Generating' : 'Send'}
        disabled={!canSubmit}
        onClick={onSubmit}
      >
        <SendHorizontal size={16} strokeWidth={1.8} aria-hidden="true" />
      </button>
    </div>
  )
}
