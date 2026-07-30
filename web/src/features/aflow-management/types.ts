export type DiagnosticState = 'valid' | 'not_valid' | 'uncertainty'

export interface ProviderProbe {
  provider: string
  model: string
  checked_at: string
  diagnostic_state: DiagnosticState
  outcome: string
  elapsed_ms: number
  detail?: string
}

export interface AflowStatus {
  workspace_id: string
  enabled: boolean
  can_submit: boolean
  primary: {
    provider: string
    model: string
    configuration_source: string
    configured: boolean
    endpoint_reachable: boolean | DiagnosticState
    model_ready: boolean | DiagnosticState
    structured_output_probe: DiagnosticState
    missing_non_secret_fields: string[]
    last_probe?: ProviderProbe
  }
  fallback: {
    provider: string | null
    explicitly_configured: boolean
    configured: boolean
    credential_configured: boolean
    missing_non_secret_fields: string[]
    structured_output_probe: DiagnosticState
    ready: boolean
    last_probe?: ProviderProbe
  }
  current_run: null | {
    lifecycle_run_id?: string
    stage?: string
    provider?: string
    provider_attempt?: number
    elapsed_seconds?: number
    remaining_seconds?: number
  }
  budgets_seconds: {
    primary: number
    fallback: number
    reserved: number
    stage_total: number
  }
}

export interface AflowIssueSummary {
  issue_id: string
  workspace_id: string
  artifact_id: string
  artifact_revision: number | null
  lifecycle_run_id: string | null
  submission_digest: string | null
  operation_id: string | null
  created_at: string
  issue_code: string
  stage: string
  diagnostic_state: DiagnosticState
  retry: {
    supported: boolean
    owning_execution_authority: string
    permitted_provider: string
    linked_retry_run: string | null
    instruction: string
  }
}

export interface AflowIssue extends AflowIssueSummary {
  explanation: {
    symptom: string
    direct_cause: string
    underlying_cause: string
    impact: string
    blocked_action: string
    checked: string[]
    remains_unknown: string[]
    evidence_required: string[]
    next_diagnostic_action: string
  }
  evidence: {
    provider_attempts: Array<Record<string, unknown>>
    bounded_error_detail: string
  }
  resolution: {
    steps: string[]
    prerequisites: string[]
    expected_successful_resolution: string
    automatic_actions_attempted: string[]
  }
}

export interface AflowIssuePage {
  workspace_id: string
  items: AflowIssueSummary[]
  next_cursor: string | null
  total: number
}
