/**
 * TerminalPane — read-only ANSI-stripped output stream.
 *
 * Constraint (documented, not hidden): `@webcontainer/api` exposes one
 * TTY-merged `output` stream per process, so stdout/stderr cannot be
 * separated; lines carry a source channel (`system` / `install` / `dev`)
 * plus an error flag instead.
 */

import { useEffect, useRef, useState } from 'react'
import type { PointerEvent as ReactPointerEvent, UIEvent } from 'react'
import { ChevronDown, ChevronUp, Copy, Trash2 } from 'lucide-react'
import { MIN_TERMINAL_HEIGHT, useWebRuntimeStore } from '../store'
import type { TerminalLine } from '../types'
import styles from '../WebRuntime.module.css'

/** Terminal may take at most this fraction of the panel height (R7). */
const MAX_HEIGHT_RATIO = 0.7

const SOURCE_CLASS: Record<TerminalLine['source'], string> = {
  system: styles.lineSystem,
  install: styles.lineInstall,
  dev: styles.lineDev,
}

export function TerminalPane() {
  const lines = useWebRuntimeStore((s) => s.lines)
  const height = useWebRuntimeStore((s) => s.terminalHeight)
  const collapsed = useWebRuntimeStore((s) => s.terminalCollapsed)
  const clearLines = useWebRuntimeStore((s) => s.clearLines)
  const setTerminalHeight = useWebRuntimeStore((s) => s.setTerminalHeight)
  const toggleCollapsed = useWebRuntimeStore((s) => s.toggleTerminalCollapsed)

  const bodyRef = useRef<HTMLDivElement>(null)
  // Auto-scroll stays pinned to the bottom until the user scrolls up.
  const pinnedRef = useRef(true)
  const dragRef = useRef<{ pointerId: number; startY: number; startHeight: number } | null>(null)
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    if (pinnedRef.current && bodyRef.current) {
      bodyRef.current.scrollTop = bodyRef.current.scrollHeight
    }
  }, [lines])

  const handleScroll = (event: UIEvent<HTMLDivElement>) => {
    const el = event.currentTarget
    pinnedRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 8
  }

  const handleCopy = async () => {
    await navigator.clipboard.writeText(lines.map((line) => line.text).join('\n'))
    setCopied(true)
    window.setTimeout(() => setCopied(false), 1500)
  }

  const handleDragStart = (event: ReactPointerEvent<HTMLDivElement>) => {
    dragRef.current = { pointerId: event.pointerId, startY: event.clientY, startHeight: height }
    event.currentTarget.setPointerCapture(event.pointerId)
  }

  const handleDragMove = (event: ReactPointerEvent<HTMLDivElement>) => {
    const drag = dragRef.current
    if (!drag || drag.pointerId !== event.pointerId) return
    // Handle sits on top of the terminal: dragging up grows it.
    const proposed = drag.startHeight + (drag.startY - event.clientY)
    const panel = event.currentTarget.closest(`.${styles.panel}`)
    const maxHeight = panel ? Math.round(panel.clientHeight * MAX_HEIGHT_RATIO) : MIN_TERMINAL_HEIGHT
    setTerminalHeight(Math.min(Math.max(proposed, MIN_TERMINAL_HEIGHT), Math.max(maxHeight, MIN_TERMINAL_HEIGHT)))
  }

  const handleDragEnd = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (dragRef.current?.pointerId === event.pointerId) dragRef.current = null
  }

  return (
    <section className={styles.terminal} aria-label="Runtime terminal">
      {!collapsed ? (
        <div
          className={styles.terminalHandle}
          role="separator"
          aria-orientation="horizontal"
          aria-label="Resize terminal"
          onPointerDown={handleDragStart}
          onPointerMove={handleDragMove}
          onPointerUp={handleDragEnd}
          onPointerCancel={handleDragEnd}
        />
      ) : null}
      <div className={styles.terminalHeader}>
        <span className={styles.terminalTitle}>Terminal</span>
        <span className={styles.spacer} />
        <button type="button" className={styles.terminalAction} onClick={clearLines}>
          <Trash2 size={12} aria-hidden="true" />
          Clear
        </button>
        <button type="button" className={styles.terminalAction} onClick={() => void handleCopy()}>
          <Copy size={12} aria-hidden="true" />
          {copied ? 'Copied' : 'Copy'}
        </button>
        <button
          type="button"
          className={styles.terminalAction}
          aria-expanded={!collapsed}
          onClick={toggleCollapsed}
        >
          {collapsed ? <ChevronUp size={12} aria-hidden="true" /> : <ChevronDown size={12} aria-hidden="true" />}
          {collapsed ? 'Expand' : 'Collapse'}
        </button>
      </div>
      {!collapsed ? (
        <div ref={bodyRef} className={styles.terminalBody} style={{ height }} onScroll={handleScroll}>
          {lines.map((line) => (
            <div
              key={line.id}
              className={`${styles.line} ${line.isError ? styles.lineError : SOURCE_CLASS[line.source]}`}
            >
              <span className={styles.linePrefix}>[{line.source}]</span>
              {line.text}
            </div>
          ))}
        </div>
      ) : null}
    </section>
  )
}
