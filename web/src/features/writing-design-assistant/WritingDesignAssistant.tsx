/**
 * WritingDesignAssistant — feature root.
 *
 * Orchestrates mode selection, input, submission to the assistant
 * backend, result rendering, recheck, and session history. The feature
 * is advisory only: it never touches files and only ever calls the
 * assistant backend endpoint.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { History } from 'lucide-react'
import { ModeSelector } from './components/ModeSelector'
import { ModelSelector } from './components/ModelSelector'
import { AssistantInput, type AssistantInputValue } from './components/AssistantInput'
import { AssistantStatus } from './components/AssistantStatus'
import { AssistantResult } from './components/AssistantResult'
import { HistoryDrawer } from './components/HistoryDrawer'
import {
  fetchAssistantModels,
  submitAssistantRequest,
  type SubmitOptions,
} from './services/assistantApi'
import { createHistoryStore, toPreview } from './storage/history'
import type {
  AssistantMode,
  AssistantModelsResponse,
  AssistantResponse,
  HistoryEntry,
  HistoryStore,
} from './types'
import styles from './WritingDesignAssistant.module.css'

const EMPTY_INPUT: AssistantInputValue = { text: '', selectedText: '', context: '', tone: '' }

export interface WritingDesignAssistantProps {
  /** Injected in tests; defaults to a fresh in-memory store. */
  historyStore?: HistoryStore
  /** Injected in tests; forwarded to the API service. */
  submitOptions?: SubmitOptions
}

export function WritingDesignAssistant({ historyStore, submitOptions }: WritingDesignAssistantProps) {
  const storeRef = useRef<HistoryStore>(historyStore ?? createHistoryStore())
  const [mode, setMode] = useState<AssistantMode>('fix_wording')
  const [input, setInput] = useState<AssistantInputValue>(EMPTY_INPUT)
  const [loading, setLoading] = useState(false)
  const [response, setResponse] = useState<AssistantResponse | null>(null)
  const [historyOpen, setHistoryOpen] = useState(false)
  const [historyVersion, setHistoryVersion] = useState(0)
  const [models, setModels] = useState<AssistantModelsResponse | null>(null)
  // '' means the provider's configured default model.
  const [model, setModel] = useState('')

  // Best-effort model listing; the selector stays hidden when it fails.
  useEffect(() => {
    let cancelled = false
    void fetchAssistantModels(submitOptions)
      .then((listing) => {
        if (!cancelled) setModels(listing)
      })
      .catch(() => {
        // Listing is advisory; never surface a failure for it.
      })
    return () => {
      cancelled = true
    }
  }, [submitOptions])

  const runRequest = useCallback(
    async (requestMode: AssistantMode, requestInput: AssistantInputValue) => {
      setLoading(true)
      try {
        const result = await submitAssistantRequest(
          {
            mode: requestMode,
            text: requestInput.text,
            selectedText: requestInput.selectedText || undefined,
            context: requestInput.context || undefined,
            tone: requestInput.tone || undefined,
            model: model || undefined,
          },
          submitOptions,
        )
        setResponse(result)
        const entry: HistoryEntry = {
          request_id: result.request_id,
          mode: requestMode,
          status: result.status,
          created_at: new Date().toISOString(),
          input_preview: toPreview(requestInput.text),
          response: result,
        }
        storeRef.current.add(entry)
        setHistoryVersion((version) => version + 1)
      } finally {
        setLoading(false)
      }
    },
    [model, submitOptions],
  )

  const handleSubmit = useCallback(() => {
    if (!input.text.trim() || loading) return
    void runRequest(mode, input)
  }, [input, loading, mode, runRequest])

  const handleRecheck = useCallback(() => {
    if (!response || loading) return
    void runRequest(response.mode, input)
  }, [input, loading, response, runRequest])

  const handleRefine = useCallback((draft: string) => {
    if (!response || response.mode !== 'fix_wording' || loading) return
    void runRequest('fix_wording', { ...input, text: draft })
  }, [input, loading, response, runRequest])

  const handleClearHistory = useCallback(() => {
    storeRef.current.clear()
    setHistoryVersion((version) => version + 1)
  }, [])

  const handleSelectEntry = useCallback((entry: HistoryEntry) => {
    setMode(entry.mode)
    setResponse(entry.response)
    setHistoryOpen(false)
  }, [])

  // historyVersion invalidates this read after add/clear.
  void historyVersion
  const historyEntries = storeRef.current.list()

  return (
    <div className={styles.assistant}>
      <header className={styles.header}>
        <div>
          <h2 className={styles.title}>Writing &amp; Design Assistant</h2>
          <p className={styles.subtitle}>
            Advisory only — results are reviewable and copyable, never applied automatically.
          </p>
        </div>
        <button
          type="button"
          className={styles.historyToggle}
          aria-label="Toggle history"
          aria-expanded={historyOpen}
          onClick={() => setHistoryOpen((open) => !open)}
        >
          <History size={16} aria-hidden="true" />
          History
        </button>
      </header>

      <ModeSelector active={mode} disabled={loading} onSelect={setMode} />
      <ModelSelector models={models} value={model} disabled={loading} onChange={setModel} />
      <AssistantInput
        mode={mode}
        value={input}
        loading={loading}
        onChange={setInput}
        onSubmit={handleSubmit}
      />
      <AssistantStatus loading={loading} response={response} />
      {response && !loading ? (
        <>
          <AssistantResult response={response} onRefine={handleRefine} />
          <button
            type="button"
            className={styles.recheckButton}
            onClick={handleRecheck}
          >
            Recheck
          </button>
        </>
      ) : null}

      <HistoryDrawer
        open={historyOpen}
        entries={historyEntries}
        onClose={() => setHistoryOpen(false)}
        onClear={handleClearHistory}
        onSelect={handleSelectEntry}
      />
    </div>
  )
}
