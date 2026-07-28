/**
 * AssistantResult — dispatches a completed/uncertain envelope to the
 * matching mode renderer. Also hosts the shared CopyButton used by all
 * copy actions. Output is advisory: reviewable and copyable only, never
 * applied to files automatically.
 */

import { useState } from 'react'
import { Check, Copy } from 'lucide-react'
import type {
  AssistantResponse,
  DraftThreeRepliesResult,
  ExpandIdeaResult,
  FixWordingResult,
  ProviderInfo,
  TeachClearlyResult,
  TranslateSlangJargonResult,
  VisualizeDesignResult,
} from '../types'
import { isErrorResult, isSelectionRequired } from '../types'
import { ChangeList } from './ChangeList'
import { DiagramPreview } from './DiagramPreview'
import { ReplyOptions } from './ReplyOptions'
import styles from '../WritingDesignAssistant.module.css'

/**
 * ProviderBadge — provenance for every rendered result. Making the active
 * provider visible is part of the correctness contract: a fake or stale
 * provider must never be mistaken for a real model. Null-safe. For
 * fix_wording the executing engine and fallback state are shown too —
 * provider identity alone never encodes fallback.
 */
export function ProviderBadge({
  provider,
  engine,
  fallbackUsed,
}: {
  provider: ProviderInfo | null
  engine?: 'model' | 'languagetool' | null
  fallbackUsed?: boolean
}) {
  if (!provider || !provider.id) return null
  return (
    <span className={styles.providerBadge} data-testid="provider-badge">
      {provider.id} · {provider.source}
      {engine ? ` · ${engine}` : ''}
      {fallbackUsed ? ' · fallback' : ''}
    </span>
  )
}

export function CopyButton({ text, label }: { text: string; label: string }) {
  const [copied, setCopied] = useState(false)
  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      // Clipboard unavailable (permissions/jsdom); leave state unchanged.
    }
  }
  return (
    <button type="button" className={styles.copyButton} aria-label={label} onClick={handleCopy}>
      {copied ? <Check size={14} aria-hidden="true" /> : <Copy size={14} aria-hidden="true" />}
      {copied ? 'Copied' : 'Copy'}
    </button>
  )
}

interface AssistantResultProps {
  response: AssistantResponse
  onRefine?: (text: string) => void
}

export function AssistantResult({ response, onRefine }: AssistantResultProps) {
  const { result } = response

  if (isErrorResult(result)) return null

  if (isSelectionRequired(result)) {
    return (
      <div className={styles.selectionRequired} role="status">
        <p>{result.message}</p>
      </div>
    )
  }

  return (
    <section className={styles.resultPanel} aria-label="Assistant result">
      <ProviderBadge
        provider={response.provider}
        engine={response.engine}
        fallbackUsed={response.fallback_used}
      />
      {response.warnings.length > 0 ? (
        <ul className={styles.warningList} aria-label="Warnings">
          {response.warnings.map((warning) => (
            <li key={warning}>{warning}</li>
          ))}
        </ul>
      ) : null}
      {renderByMode(response, onRefine)}
    </section>
  )
}

function renderByMode(response: AssistantResponse, onRefine?: (text: string) => void) {
  switch (response.mode) {
    case 'fix_wording': {
      const result = response.result as FixWordingResult
      return <ImprovedMessageResult result={result} onRefine={onRefine} />
    }
    case 'draft_three_replies':
      return <ReplyOptions result={response.result as DraftThreeRepliesResult} />
    case 'translate_slang_jargon': {
      const result = response.result as TranslateSlangJargonResult
      return (
        <div className={styles.translation}>
          <div className={styles.codeBlockHeader}>
            <h3 className={styles.resultHeading}>“{result.term}”</h3>
            <CopyButton
              text={result.professional_translation}
              label="Copy professional translation"
            />
          </div>
          <p>
            <strong>Professional:</strong> {result.professional_translation}
          </p>
          <p>
            <strong>Plain meaning:</strong> {result.plain_meaning}
          </p>
          <p>
            <strong>Origin:</strong> {result.origin_context}
          </p>
          {(result.usage_notes ?? []).length > 0 ? (
            <ul aria-label="Usage notes">
              {(result.usage_notes ?? []).map((note) => (
                <li key={note}>{note}</li>
              ))}
            </ul>
          ) : null}
          <blockquote className={styles.translationExample}>
            <p>“{result.example.original}”</p>
            <p>→ “{result.example.professional}”</p>
          </blockquote>
        </div>
      )
    }
    case 'teach_clearly': {
      const result = response.result as TeachClearlyResult
      return (
        <div className={styles.teaching}>
          <h3 className={styles.resultHeading}>The basics</h3>
          <p>{result.basics}</p>
          {(result.building_from_there ?? []).length > 0 ? (
            <>
              <h3 className={styles.resultHeading}>Building from there</h3>
              <ol>
                {(result.building_from_there ?? []).map((step) => (
                  <li key={step}>{step}</li>
                ))}
              </ol>
            </>
          ) : null}
          {(result.key_insights ?? []).length > 0 ? (
            <>
              <h3 className={styles.resultHeading}>Key insights</h3>
              <ul>
                {(result.key_insights ?? []).map((insight) => (
                  <li key={insight}>{insight}</li>
                ))}
              </ul>
            </>
          ) : null}
          {(result.common_misconceptions ?? []).length > 0 ? (
            <>
              <h3 className={styles.resultHeading}>Common misconceptions</h3>
              <ul>
                {(result.common_misconceptions ?? []).map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </>
          ) : null}
          <h3 className={styles.resultHeading}>Why this matters</h3>
          <p>{result.why_this_matters}</p>
          {(result.check_your_understanding ?? []).length > 0 ? (
            <>
              <h3 className={styles.resultHeading}>Check your understanding</h3>
              <ul>
                {(result.check_your_understanding ?? []).map((question) => (
                  <li key={question}>{question}</li>
                ))}
              </ul>
            </>
          ) : null}
        </div>
      )
    }
    case 'expand_idea': {
      const result = response.result as ExpandIdeaResult
      return (
        <div>
          <div className={styles.codeBlockHeader}>
            <h3 className={styles.resultHeading}>Expanded idea</h3>
            <CopyButton text={result.expanded_text} label="Copy expanded text" />
          </div>
          <p className={styles.correctedText}>{result.expanded_text}</p>
          <p>
            <strong>Preserved intent:</strong> {result.preserved_intent}
          </p>
          {(result.added_assumptions ?? []).length > 0 ? (
            <>
              <h3 className={styles.resultHeading}>Added assumptions</h3>
              <ul aria-label="Added assumptions">
                {(result.added_assumptions ?? []).map((assumption) => (
                  <li key={assumption}>{assumption}</li>
                ))}
              </ul>
            </>
          ) : null}
        </div>
      )
    }
    case 'visualize_design':
      return <DiagramPreview result={response.result as VisualizeDesignResult} />
    default:
      return null
  }
}

function ImprovedMessageResult({
  result,
  onRefine,
}: {
  result: FixWordingResult
  onRefine?: (text: string) => void
}) {
  const [draft, setDraft] = useState(result.corrected_text)
  return (
    <div className={styles.intentResult}>
      <div className={styles.codeBlockHeader}>
        <h3 className={styles.resultHeading}>Improved message</h3>
        <CopyButton text={draft} label="Copy improved message" />
      </div>
      <textarea
        className={styles.resultEditor}
        aria-label="Improved message"
        value={draft}
        onChange={(event) => setDraft(event.target.value)}
        rows={6}
      />
      <button type="button" className={styles.recheckButton} disabled={!draft.trim()} onClick={() => onRefine?.(draft)}>
        Refine this message
      </button>
      {result.inferred_intent ? <p><strong>Intent:</strong> {result.inferred_intent}</p> : null}
      {result.tone ? <p><strong>Tone:</strong> {result.tone}</p> : null}
      {result.context ? <p><strong>Context:</strong> {result.context}</p> : null}
      {(result.assumptions ?? []).length > 0 ? <><h3 className={styles.resultHeading}>Assumptions</h3><ul>{result.assumptions?.map((item) => <li key={item}>{item}</li>)}</ul></> : null}
      {(result.uncertainty ?? []).length > 0 ? <><h3 className={styles.resultHeading}>Uncertainty</h3><ul>{result.uncertainty?.map((item) => <li key={item}>{item}</li>)}</ul></> : null}
      <ChangeList changes={result.changes ?? []} noChangesNeeded={result.no_changes_needed ?? false} />
    </div>
  )
}
