import type { UsageAccountingEvidence } from '../usageAccounting'
import styles from './UsagePanel.module.css'

function tokenValue(value: number | null | undefined): string {
  return value == null ? 'Unavailable' : value.toLocaleString()
}

/**
 * reservedOutputValue — the wire contract guarantees
 * reserved_output_tokens is an integer >= 0 whenever `estimate` is
 * present. Zero is a valid reservation (user configured
 * max_output_tokens=0), NOT absence. Absence of any reservation is
 * signalled by `estimate` itself being null/undefined, in which case
 * the panel renders "Not reserved" rather than a misleading "0".
 */
function reservedOutputValue(estimate: { reserved_output_tokens: number } | null | undefined): string {
  if (!estimate) return 'Not reserved'
  return estimate.reserved_output_tokens.toLocaleString()
}

function costValue(value: string | null | undefined): string {
  return value == null ? 'Pricing unavailable' : `$${value} USD`
}

export function UsagePanel({ usage }: { usage: UsageAccountingEvidence }) {
  if (usage.accounting_status === 'not_applicable') {
    return (
      <section className={styles.panel} aria-label="Usage accounting">
        <h3 className={styles.heading}>Usage accounting</h3>
        <p>Token accounting does not apply to {usage.provider}.</p>
      </section>
    )
  }

  return (
    <section className={styles.panel} aria-label="Usage accounting">
      <h3 className={styles.heading}>Usage accounting</h3>
      <dl className={styles.grid}>
        <dt>Estimated input</dt>
        <dd className={styles.value}>{tokenValue(usage.estimate?.input_tokens)}</dd>
        <dt>Reserved output</dt>
        <dd className={styles.value}>
          {reservedOutputValue(usage.estimate ?? null)}
        </dd>
        <dt>Provider-reported input</dt>
        <dd className={styles.value}>{tokenValue(usage.actual?.input_tokens)}</dd>
        <dt>Provider-reported output</dt>
        <dd className={styles.value}>{tokenValue(usage.actual?.output_tokens)}</dd>
        <dt>Estimated maximum charge</dt>
        <dd className={styles.value}>{costValue(usage.estimated_cost?.total)}</dd>
        <dt>Actual calculated charge</dt>
        <dd className={styles.value}>{costValue(usage.actual_cost?.total)}</dd>
        <dt>Confidence</dt>
        <dd className={styles.value}>{usage.estimate?.confidence ?? 'Unavailable'}</dd>
        <dt>Accounting completeness</dt>
        <dd className={styles.value}>{usage.accounting_status}</dd>
      </dl>
      {usage.warnings.length > 0 ? (
        <p className={styles.warning}>{usage.warnings.join(' · ')}</p>
      ) : null}
    </section>
  )
}
