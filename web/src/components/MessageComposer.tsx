import { useRef, useCallback, useEffect, type KeyboardEvent } from 'react'
import { useAppStore } from '../store/taskStore'
import type { ChatCapacity } from '../agent/chatCapacity'
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

function formatTokens(value: number | null): string {
  return value == null ? '–' : value.toLocaleString('en-US')
}

/**
 * Live capacity meter — estimated next request input vs usable input
 * allowance (context limit − reserved output). Pre-send estimate only;
 * completed-turn IN/OUT usage stays on the TokenBadge, never here.
 */
function CapacityMeter({ capacity }: { capacity: ChatCapacity }) {
  if (capacity.status === 'unavailable') {
    return (
      <span
        className={`${styles.meter} ${styles.meterUnavailable}`}
        role="status"
        aria-label="Token capacity"
        title="Token capacity estimate is unavailable — the backend could not be reached"
      >
        capacity unavailable
      </span>
    )
  }

  const stateClass = capacity.overLimit
    ? styles.meterOver
    : capacity.nearLimit
      ? styles.meterWarn
      : ''
  const allowance =
    capacity.usableInput != null ? formatTokens(capacity.usableInput) : 'unknown'

  return (
    <span
      className={`${styles.meter} ${stateClass}`}
      role="status"
      aria-label="Token capacity"
      title="Estimated next request input / usable input allowance (context limit − reserved output)"
    >
      {formatTokens(capacity.estimatedInput)} / {allowance}
    </span>
  )
}

export function MessageComposer({
  onSend,
  anchorMode,
  onAnchorModeChange,
  disabled = false,
}: MessageComposerProps) {
  const value = useAppStore((s) => s.draft)
  const setDraft = useAppStore((s) => s.setDraft)
  const capacity = useAppStore((s) => s.capacity)
  const requestEstimateNow = useAppStore((s) => s.requestEstimateNow)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const wasDisabledRef = useRef(disabled)

  // Baseline estimate on mount: an empty draft still costs the system
  // prompt and any history that will be sent.
  useEffect(() => {
    requestEstimateNow()
  }, [requestEstimateNow])

  // Return focus to the input when the turn comes back to the user
  useEffect(() => {
    if (wasDisabledRef.current && !disabled) {
      textareaRef.current?.focus()
    }
    wasDisabledRef.current = disabled
  }, [disabled])

  const handleSubmit = useCallback(() => {
    if (disabled || capacity.overLimit || !value.trim()) return
    onSend(value)
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto'
    }
  }, [value, onSend, disabled, capacity.overLimit])

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
        <div className={styles.inputRow}>
          <textarea
            ref={textareaRef}
            className={styles.textarea}
            placeholder={disabled ? 'Waiting for the assistant…' : 'Message Audisor...'}
            value={value}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={handleKeyDown}
            onInput={handleInput}
            rows={1}
            disabled={disabled}
            aria-disabled={disabled}
          />
          <CapacityMeter capacity={capacity} />
        </div>
        {capacity.nearLimit && !capacity.overLimit ? (
          <div className={styles.capacityNote} role="alert">
            Approaching the model&apos;s context limit —{' '}
            {formatTokens(capacity.estimatedInput)} of{' '}
            {formatTokens(capacity.usableInput)} usable input tokens.
          </div>
        ) : null}
        {capacity.overLimit ? (
          <div className={`${styles.capacityNote} ${styles.capacityBlocked}`} role="alert">
            Sending is blocked: the estimated input of{' '}
            {formatTokens(capacity.estimatedInput)} tokens exceeds the usable
            allowance of {formatTokens(capacity.usableInput)} (context limit{' '}
            {formatTokens(capacity.contextLimit)} − reserved output{' '}
            {formatTokens(capacity.reservedOutput)}). Shorten the message or
            start a new conversation.
          </div>
        ) : null}
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
            className={`${styles.sendBtn} ${value.trim() && !disabled && !capacity.overLimit ? styles.sendActive : ''}`}
            onClick={handleSubmit}
            title={
              disabled
                ? 'Waiting for the assistant'
                : capacity.overLimit
                  ? 'Estimated input exceeds the usable context allowance'
                  : 'Send message'
            }
            disabled={disabled || !value.trim() || capacity.overLimit}
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
