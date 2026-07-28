/**
 * AssistantStatus — loading, failure, and uncertainty banners.
 * Failure messages are already sanitized by the backend; this component
 * only ever renders the normalized category + message.
 */

import { AlertTriangle, HelpCircle, Loader2 } from 'lucide-react'
import type { AssistantResponse } from '../types'
import { isErrorResult } from '../types'
import { ProviderBadge } from './AssistantResult'
import styles from '../WritingDesignAssistant.module.css'

interface AssistantStatusProps {
  loading: boolean
  response: AssistantResponse | null
}

const CATEGORY_LABELS: Record<string, string> = {
  configuration: 'The assistant backend is not configured.',
  unavailable: 'The assistant backend is unavailable.',
  timeout: 'The request timed out.',
  authentication: 'Authentication failed.',
  rate_limited: 'Too many requests right now.',
  invalid_response: 'The model returned an unusable response.',
  unsupported: 'This request is not supported.',
  internal: 'An internal error occurred.',
}

export function AssistantStatus({ loading, response }: AssistantStatusProps) {
  if (loading) {
    return (
      <div className={styles.statusLoading} role="status" aria-live="polite">
        <Loader2 size={16} className={styles.spinner} aria-hidden="true" />
        Working on it…
      </div>
    )
  }
  if (!response) return null

  if (response.status === 'failed' && isErrorResult(response.result)) {
    const { category, message } = response.result.error
    return (
      <div className={styles.statusError} role="alert">
        <AlertTriangle size={16} aria-hidden="true" />
        <div>
          <strong>{CATEGORY_LABELS[category] ?? 'The request failed.'}</strong>
          {message ? <p className={styles.statusDetail}>{message}</p> : null}
          <ProviderBadge
            provider={response.provider}
            engine={response.engine}
            fallbackUsed={response.fallback_used}
          />
        </div>
      </div>
    )
  }

  if (response.status === 'uncertainty' && response.uncertainty.length > 0) {
    return (
      <div className={styles.statusUncertain} role="status">
        <HelpCircle size={16} aria-hidden="true" />
        <ul className={styles.uncertaintyList}>
          {response.uncertainty.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      </div>
    )
  }

  return null
}
