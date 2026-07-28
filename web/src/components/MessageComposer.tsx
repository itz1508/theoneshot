import { useState, useRef, useCallback, useEffect, type KeyboardEvent } from 'react'
import styles from './MessageComposer.module.css'

/** Which row anchors a new turn. */
export type AnchorMode = 'user' | 'assistant'

const ANCHOR_MODES: { value: AnchorMode; label: string }[] = [
  { value: 'user', label: 'User' },
  { value: 'assistant', label: 'Assistant' },
]

interface MessageComposerProps {
  onSend: (text: string) => void
  anchorMode: AnchorMode
  onAnchorModeChange: (mode: AnchorMode) => void
  /** Turn manager: true while it is the agent's turn. */
  disabled?: boolean
}

export function MessageComposer({
  onSend,
  anchorMode,
  onAnchorModeChange,
  disabled = false,
}: MessageComposerProps) {
  const [value, setValue] = useState('')
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const wasDisabledRef = useRef(disabled)

  // Return focus to the input when the turn comes back to the user
  useEffect(() => {
    if (wasDisabledRef.current && !disabled) {
      textareaRef.current?.focus()
    }
    wasDisabledRef.current = disabled
  }, [disabled])

  const handleSubmit = useCallback(() => {
    if (disabled || !value.trim()) return
    onSend(value)
    setValue('')
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto'
    }
  }, [value, onSend, disabled])

  const handleKeyDown = useCallback(
    (e: KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault()
        handleSubmit()
      }
    },
    [handleSubmit],
  )

  const handleInput = useCallback(() => {
    const el = textareaRef.current
    if (el) {
      el.style.height = 'auto'
      el.style.height = Math.min(el.scrollHeight, 200) + 'px'
    }
  }, [])

  return (
    <div className={styles.composer}>
      <div className={`${styles.inner} ${disabled ? styles.innerDisabled : ''}`}>
        <textarea
          ref={textareaRef}
          className={styles.textarea}
          placeholder={disabled ? 'Waiting for the assistant…' : 'Message Audisor...'}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={handleKeyDown}
          onInput={handleInput}
          rows={1}
          disabled={disabled}
          aria-disabled={disabled}
        />
        <div className={styles.actions}>
          <button className={styles.actionBtn} title="Attach file">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
              <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48" />
            </svg>
          </button>
          <button className={styles.modeBtn} title="Mode selector">
            <span>Auto</span>
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <polyline points="6 9 12 15 18 9" />
            </svg>
          </button>
          <div className={styles.anchorGroup} role="group" aria-label="Anchor new turns on">
            {ANCHOR_MODES.map((mode) => (
              <button
                key={mode.value}
                type="button"
                className={`${styles.anchorBtn} ${anchorMode === mode.value ? styles.anchorActive : ''}`}
                aria-pressed={anchorMode === mode.value}
                title={`Anchor new turns on: ${mode.label}`}
                onClick={() => onAnchorModeChange(mode.value)}
              >
                {mode.label}
              </button>
            ))}
          </div>
          <button
            className={`${styles.sendBtn} ${value.trim() && !disabled ? styles.sendActive : ''}`}
            onClick={handleSubmit}
            title={disabled ? 'Waiting for the assistant' : 'Send message'}
            disabled={disabled || !value.trim()}
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <line x1="22" y1="2" x2="11" y2="13" />
              <polygon points="22 2 15 22 11 13 2 9 22 2" />
            </svg>
          </button>
        </div>
      </div>
    </div>
  )
}
