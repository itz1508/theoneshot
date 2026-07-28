/**
 * DiagramPreview — renders sanitized Mermaid code locally.
 *
 * Mermaid is a pinned local dependency loaded via dynamic import;
 * nothing is ever fetched from a CDN. If rendering fails (unsupported
 * syntax, no layout engine), the sanitized code block remains visible.
 */

import { useEffect, useRef, useState } from 'react'
import type { VisualizeDesignResult } from '../types'
import { CopyButton } from './AssistantResult'
import styles from '../WritingDesignAssistant.module.css'

interface DiagramPreviewProps {
  result: VisualizeDesignResult
}

let diagramCounter = 0

export function DiagramPreview({ result }: DiagramPreviewProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const [renderFailed, setRenderFailed] = useState(false)

  useEffect(() => {
    let cancelled = false
    setRenderFailed(false)

    async function render() {
      try {
        const { default: mermaid } = await import('mermaid')
        mermaid.initialize({ startOnLoad: false, securityLevel: 'strict', theme: 'dark' })
        diagramCounter += 1
        const { svg } = await mermaid.render(`wda-diagram-${diagramCounter}`, result.diagram_code)
        if (!cancelled && containerRef.current) {
          containerRef.current.innerHTML = svg
        }
      } catch {
        if (!cancelled) setRenderFailed(true)
      }
    }

    void render()
    return () => {
      cancelled = true
    }
  }, [result.diagram_code])

  return (
    <div className={styles.diagramPreview}>
      <p className={styles.diagramSummary}>{result.summary}</p>
      {!renderFailed ? (
        <div
          ref={containerRef}
          className={styles.diagramCanvas}
          role="img"
          aria-label="Design diagram preview"
        />
      ) : null}
      <div className={styles.codeBlockWrap}>
        <div className={styles.codeBlockHeader}>
          <span>Diagram code</span>
          <CopyButton text={result.diagram_code} label="Copy diagram code" />
        </div>
        <pre className={styles.codeBlock}>
          <code>{result.diagram_code}</code>
        </pre>
      </div>
      <div className={styles.codeBlockWrap}>
        <div className={styles.codeBlockHeader}>
          <span>Builder prompt</span>
          <CopyButton text={result.builder_prompt} label="Copy builder prompt" />
        </div>
        <pre className={styles.codeBlock}>
          <code>{result.builder_prompt}</code>
        </pre>
      </div>
      {(result.warnings ?? []).length > 0 ? (
        <ul className={styles.diagramWarnings} aria-label="Diagram warnings">
          {(result.warnings ?? []).map((warning) => (
            <li key={warning}>{warning}</li>
          ))}
        </ul>
      ) : null}
    </div>
  )
}
