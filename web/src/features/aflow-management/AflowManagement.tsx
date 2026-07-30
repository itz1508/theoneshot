import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { CheckCircle2, CircleAlert, Clipboard, Cloud, RefreshCw, Server } from 'lucide-react'
import { getAflowIssue, probeAflowProviders, retryAflowIssue } from './api'
import type { AflowIssue, AflowIssuePage, AflowStatus } from './types'
import { RootCauseResolutionDetails } from '../../components/RootCauseResolutionDetails'
import styles from './AflowManagement.module.css'

interface Props {
  status: AflowStatus | null
  issues: AflowIssuePage | null
  error: string | null
  onRefresh: () => Promise<void>
}

function State({ value }: { value: boolean | string }) {
  const valid = value === true || value === 'valid'
  const label = typeof value === 'boolean' ? (value ? 'valid' : 'not valid') : value.replace('_', ' ')
  return <span className={valid ? styles.valid : styles.uncertain}>{label}</span>
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className={styles.detailSection}>
      <h3>{title}</h3>
      {children}
    </section>
  )
}

export default function AflowManagement({ status, issues, error, onRefresh }: Props) {
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [selected, setSelected] = useState<AflowIssue | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)

  useEffect(() => {
    if (!selectedId && issues?.items[0]) setSelectedId(issues.items[0].issue_id)
  }, [issues, selectedId])

  useEffect(() => {
    if (!selectedId) {
      setSelected(null)
      return
    }
    void getAflowIssue(selectedId).then(setSelected).catch((cause) => setActionError(String(cause)))
  }, [selectedId])

  const localDisabled = useMemo(() => {
    if (!selected) return 'Select an issue.'
    if (!selected.retry.supported) return selected.retry.instruction
    if (!selected.operation_id) return 'A linked OperationController operation is required.'
    return null
  }, [selected])
  const fallbackMissing = [
    ...(!status?.fallback.credential_configured ? ['server-side Fireworks credential'] : []),
    ...(status?.fallback.missing_non_secret_fields ?? []),
  ]
  const fallbackDisabled = localDisabled ?? (!status?.fallback.ready
    ? `Fireworks is not ready. Missing: ${fallbackMissing.join(', ') || 'a valid structured-output probe'}.`
    : null)

  const runAction = async (action: 'probe' | 'local' | 'fallback' | 'copy') => {
    setBusy(action)
    setActionError(null)
    try {
      if (action === 'probe') await probeAflowProviders()
      if (action === 'local' && selected) await retryAflowIssue(selected, 'local')
      if (action === 'fallback' && selected) await retryAflowIssue(selected, 'fallback')
      if (action === 'copy' && selected) {
        await navigator.clipboard.writeText(JSON.stringify(selected, null, 2))
      }
      await onRefresh()
    } catch (cause) {
      setActionError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className={styles.screen}>
      <header className={styles.header}>
        <div>
          <p className={styles.eyebrow}>Workspace management</p>
          <h1>A-Flow</h1>
          <p>Provider readiness, lifecycle activity, and evidence-backed recovery.</p>
        </div>
        <button className={styles.primaryButton} onClick={() => void runAction('probe')} disabled={busy !== null}>
          <RefreshCw size={16} /> Check providers
        </button>
      </header>

      {error ? <div role="alert" className={styles.error}><CircleAlert size={16} />{error}</div> : null}
      {actionError ? <div role="alert" className={styles.error}><CircleAlert size={16} />{actionError}</div> : null}

      <section className={styles.statusGrid} aria-label="Provider status">
        <article className={styles.statusCard}>
          <Server size={18} />
          <div><span>Local provider</span><strong>{status?.primary.provider ?? 'Loading'}</strong></div>
          <dl>
            <div><dt>Enabled</dt><dd><State value={status?.enabled ?? 'uncertainty'} /></dd></div>
            <div><dt>Model</dt><dd>{status?.primary.model ?? 'Unknown'}</dd></div>
            <div><dt>Configured</dt><dd><State value={status?.primary.configured ?? 'uncertainty'} /></dd></div>
            <div><dt>Reachable</dt><dd><State value={status?.primary.endpoint_reachable ?? 'uncertainty'} /></dd></div>
            <div><dt>Structured output</dt><dd><State value={status?.primary.structured_output_probe ?? 'uncertainty'} /></dd></div>
          </dl>
        </article>
        <article className={styles.statusCard}>
          <Cloud size={18} />
          <div><span>Fallback</span><strong>{status?.fallback.provider ?? 'Not configured'}</strong></div>
          <dl>
            <div><dt>Explicit</dt><dd><State value={status?.fallback.explicitly_configured ?? 'uncertainty'} /></dd></div>
            <div><dt>Ready</dt><dd><State value={status?.fallback.ready ?? 'uncertainty'} /></dd></div>
            <div><dt>Missing</dt><dd>{status?.fallback.missing_non_secret_fields.join(', ') || 'None'}</dd></div>
          </dl>
        </article>
        <article className={styles.statusCard}>
          <CheckCircle2 size={18} />
          <div><span>Current lifecycle</span><strong>{status?.current_run?.stage ?? 'Idle'}</strong></div>
          <dl>
            <div><dt>Provider</dt><dd>{status?.current_run?.provider ?? 'None'}</dd></div>
            <div><dt>Attempt</dt><dd>{status?.current_run?.provider_attempt ?? '—'}</dd></div>
            <div><dt>Elapsed</dt><dd>{status?.current_run?.elapsed_seconds ?? 0}s</dd></div>
            <div><dt>Remaining</dt><dd>{status?.current_run?.remaining_seconds ?? status?.budgets_seconds.stage_total ?? 300}s</dd></div>
          </dl>
        </article>
      </section>

      <div className={styles.workspace}>
        <section className={styles.history} aria-label="A-Flow issue history">
          <div className={styles.panelHeading}><h2>Issue history</h2><span>{issues?.total ?? 0}</span></div>
          {issues?.items.length ? issues.items.map((issue) => (
            <button
              key={issue.issue_id}
              className={`${styles.issueRow} ${selectedId === issue.issue_id ? styles.selected : ''}`}
              onClick={() => setSelectedId(issue.issue_id)}
            >
              <time>{new Date(issue.created_at).toLocaleString()}</time>
              <strong>{issue.issue_code.split('_').join(' ')}</strong>
              <span>{issue.artifact_id} · {issue.stage}</span>
              <small>{issue.diagnostic_state} · {issue.retry.supported ? 'retry available' : 'originating client owns retry'}</small>
            </button>
          )) : <p className={styles.empty}>No operational A-Flow issues. Unresolved artifact gaps do not appear here.</p>}
        </section>

        <article className={styles.issueDetail} aria-live="polite">
          {selected ? (
            <>
              <div className={styles.panelHeading}><h2>{selected.issue_code.split('_').join(' ')}</h2><State value={selected.diagnostic_state} /></div>
              <Section title="What happened"><p>{selected.explanation.symptom}</p></Section>
              <Section title="Direct cause">
                <RootCauseResolutionDetails kind="cause" directCause={selected.explanation.direct_cause} />
              </Section>
              <Section title="Underlying cause"><p>{selected.explanation.underlying_cause}</p></Section>
              <Section title="Evidence">
                <pre>{JSON.stringify(selected.evidence, null, 2)}</pre>
              </Section>
              <Section title="Impact and blocked action"><p>{selected.explanation.impact} Blocked: {selected.explanation.blocked_action}.</p></Section>
              <Section title="How to fix">
                <RootCauseResolutionDetails kind="resolution" resolution={selected.resolution.steps} />
              </Section>
              <Section title="Automatic actions attempted"><p>{selected.resolution.automatic_actions_attempted.join(', ') || 'None'}</p></Section>
              <Section title="Retry prerequisites"><ul>{selected.resolution.prerequisites.map((item) => <li key={item}>{item}</li>)}</ul></Section>
              <Section title="Expected successful resolution"><p>{selected.resolution.expected_successful_resolution}</p></Section>
              <div className={styles.actions}>
                <button onClick={() => void runAction('local')} disabled={busy !== null || !!localDisabled} title={localDisabled ?? undefined}>Retry local</button>
                <button onClick={() => void runAction('fallback')} disabled={busy !== null || !!fallbackDisabled} title={fallbackDisabled ?? undefined}>Continue with Fireworks</button>
                <button onClick={() => void runAction('copy')} disabled={busy !== null}><Clipboard size={14} /> Copy redacted diagnostic bundle</button>
              </div>
              {localDisabled ? <p className={styles.prerequisite}>Retry local unavailable: {localDisabled}</p> : null}
              {fallbackDisabled ? <p className={styles.prerequisite}>Fireworks unavailable: {fallbackDisabled}</p> : null}
            </>
          ) : <p className={styles.empty}>Select an issue to inspect its direct cause, evidence, and repair path.</p>}
        </article>
      </div>
    </div>
  )
}
