/**
 * DiagramPreview — kind-aware rendering of visualize_design results.
 *
 * workflow → sanitized Mermaid code rendered locally (pinned dependency,
 * dynamic import, never a CDN); if rendering fails the code block remains.
 * layout → collapsed/expanded ASCII trees with a detail toggle.
 * unclear → summary only, explaining what is missing.
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
  const [expandedView, setExpandedView] = useState(false)

  const diagramCode = result.kind === 'workflow' ? result.diagram_code ?? '' : ''

  useEffect(() => {
    let cancelled = false
    setRenderFailed(false)
    if (!diagramCode) return undefined

    async function render() {
      try {
        const { default: mermaid } = await import('mermaid')
        mermaid.initialize({ startOnLoad: false, securityLevel: 'strict', theme: 'dark' })
        diagramCounter += 1
        const { svg } = await mermaid.render(`wda-diagram-${diagramCounter}`, diagramCode)
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
  }, [diagramCode])

  const warnings = result.warnings ?? []
  const layoutLines = expandedView ? result.expanded ?? [] : result.collapsed ?? []
  const layoutText = layoutLines.join('\n')

  return (
    <div className={styles.diagramPreview}>
      <p className={styles.diagramSummary}>{result.summary}</p>

      {result.kind === 'workflow' && diagramCode ? (
        <>
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
              <CopyButton text={diagramCode} label="Copy diagram code" />
            </div>
            <pre className={styles.codeBlock}>
              <code>{diagramCode}</code>
            </pre>
          </div>
        </>
      ) : null}

      {result.kind === 'layout' && layoutLines.length > 0 ? (
        <div className={styles.codeBlockWrap}>
          <div className={styles.codeBlockHeader}>
            <span>{expandedView ? 'Layout (detailed)' : 'Layout (overview)'}</span>
            <span>
              {(result.expanded ?? []).length > 0 ? (
                <button
                  type="button"
                  className={styles.copyButton}
                  aria-pressed={expandedView}
                  onClick={() => setExpandedView((current) => !current)}
                >
                  {expandedView ? 'Show overview' : 'Show details'}
                </button>
              ) : null}
              <CopyButton text={layoutText} label="Copy layout tree" />
            </span>
          </div>
          <pre className={styles.codeBlock} aria-label="Layout tree">
            <code>{layoutText}</code>
          </pre>
        </div>
      ) : null}

      {result.builder_prompt ? (
        <div className={styles.codeBlockWrap}>
          <div className={styles.codeBlockHeader}>
            <span>Builder prompt</span>
            <CopyButton text={result.builder_prompt} label="Copy builder prompt" />
          </div>
          <pre className={styles.codeBlock}>
            <code>{result.builder_prompt}</code>
          </pre>
        </div>
      ) : null}

      {warnings.length > 0 ? (
        <ul className={styles.diagramWarnings} aria-label="Diagram warnings">
          {warnings.map((warning) => (
            <li key={warning}>{warning}</li>
          ))}
        </ul>
      ) : null}
    </div>
  )
}
