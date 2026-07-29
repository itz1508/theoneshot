export type AccountingStatus = 'complete' | 'incomplete' | 'not_applicable'
export type UsageConfidence =
  | 'exact'
  | 'provider_compatible'
  | 'approximate'
  | 'unavailable'

export interface UsageEstimate {
  input_tokens: number
  /**
   * reserved_output_tokens — pre-request output reservation. Always an
   * integer >= 0 on the wire (backend schema: required, minimum 0). Zero
   * is a valid reservation (user configured max_output_tokens=0), NOT
   * absence. Absence of a reservation is signalled by the parent `estimate`
   * being null/undefined, not by this field being zero.
   */
  reserved_output_tokens: number
  estimated_total_tokens: number
  source: string
  confidence: UsageConfidence
  method: string
  fallback_reason?: string | null
  context_limit?: number | null
}

export interface NormalizedUsage {
  input_tokens?: number | null
  output_tokens?: number | null
  cached_read_tokens?: number | null
  cache_write_tokens?: number | null
  reasoning_tokens?: number | null
  total_tokens?: number | null
  source: string
  status: AccountingStatus
}

export interface UsageCost {
  currency: 'USD'
  uncached_input: string
  cached_read: string
  cached_write: string
  output: string
  reasoning: string
  total: string
  pricing_record_id: string
  pricing_snapshot_sha256: string
}

export interface UsageDecision {
  permitted: boolean
  reasons: string[]
  warnings: string[]
}

export interface UsageAccountingEvidence {
  schema_version: '1.0.0'
  operation_id: string
  attempt_id: string
  provider: string
  model: string
  request_hash?: string | null
  accounting_status: AccountingStatus
  estimate?: UsageEstimate | null
  actual?: NormalizedUsage | null
  estimated_cost?: UsageCost | null
  actual_cost?: UsageCost | null
  context_decision?: UsageDecision | null
  cost_decision?: UsageDecision | null
  pricing_snapshot_sha256?: string | null
  pricing_record_id?: string | null
  warnings: string[]
}
