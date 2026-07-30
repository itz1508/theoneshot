import '@testing-library/jest-dom/vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { ActivityRail } from '../../../components/ActivityRail'
import AflowManagement from '../AflowManagement'
import type { AflowIssue, AflowIssuePage, AflowStatus } from '../types'

const issue: AflowIssue = {
  issue_id: 'aflow-00000000000000000000',
  workspace_id: 'workspace',
  artifact_id: 'artifact.revision-3',
  artifact_revision: 3,
  lifecycle_run_id: 'run-timeout',
  submission_digest: 'a'.repeat(64),
  operation_id: null,
  created_at: '2026-07-29T00:00:00Z',
  issue_code: 'provider_timeout',
  stage: 'gap_finding',
  diagnostic_state: 'valid',
  retry: {
    supported: false,
    owning_execution_authority: 'originating_mcp_client',
    permitted_provider: 'local-openai-compatible',
    linked_retry_run: null,
    instruction: 'Resubmit from the originating MCP client.',
  },
  explanation: {
    symptom: 'gap_finding did not return within its provider request.',
    direct_cause: 'The selected provider timed out.',
    underlying_cause: "I don't know.",
    impact: 'The lifecycle is blocked.',
    blocked_action: 'gap_finding',
    checked: ['endpoint', 'model'],
    remains_unknown: ['model performance'],
    evidence_required: ['provider diagnostics'],
    next_diagnostic_action: 'Run the provider probe.',
  },
  evidence: { provider_attempts: [], bounded_error_detail: 'timeout' },
  resolution: {
    steps: ['Complete configuration.', 'Run provider probe.', 'Start a linked retry.'],
    prerequisites: ['Provider probe is valid'],
    expected_successful_resolution: 'The stage returns a schema-valid result.',
    automatic_actions_attempted: [],
  },
}

vi.mock('../api', () => ({
  getAflowIssue: vi.fn(async () => issue),
  probeAflowProviders: vi.fn(async () => status),
  retryAflowIssue: vi.fn(),
}))

const status: AflowStatus = {
  workspace_id: 'workspace',
  enabled: true,
  can_submit: true,
  primary: {
    provider: 'local-openai-compatible', model: 'qwen2.5-coder:7b',
    configuration_source: 'runtime_default', configured: true,
    endpoint_reachable: true, model_ready: true, structured_output_probe: 'valid',
    missing_non_secret_fields: [],
  },
  fallback: {
    provider: 'fireworks', explicitly_configured: true, configured: false,
    credential_configured: true, missing_non_secret_fields: ['FIREWORKS_BASE_URL', 'FIREWORKS_MODEL'],
    structured_output_probe: 'uncertainty', ready: false,
  },
  current_run: null,
  budgets_seconds: { primary: 120, fallback: 150, reserved: 30, stage_total: 300 },
}

const page: AflowIssuePage = {
  workspace_id: 'workspace', items: [issue], next_cursor: null, total: 1,
}

describe('A-Flow management', () => {
  it('shows an unread activity-rail badge without selecting the tab', () => {
    const onSelect = vi.fn()
    render(<ActivityRail active="explorer" onSelect={onSelect} aflowUnread={2} />)
    expect(screen.getByLabelText('2 unread A-Flow issues')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Explorer' })).toHaveAttribute('aria-pressed', 'true')
    expect(onSelect).not.toHaveBeenCalled()
  })

  it('renders issue detail in the required order with honest unknown cause', async () => {
    render(<AflowManagement status={status} issues={page} error={null} onRefresh={vi.fn(async () => undefined)} />)
    await waitFor(() => expect(screen.getByText("I don't know.")).toBeInTheDocument())
    const headings = screen.getAllByRole('heading', { level: 3 }).map((node) => node.textContent)
    expect(headings).toEqual([
      'What happened', 'Direct cause', 'Underlying cause', 'Evidence',
      'Impact and blocked action', 'How to fix', 'Automatic actions attempted',
      'Retry prerequisites', 'Expected successful resolution',
    ])
  })

  it('explains every disabled recovery action prerequisite', async () => {
    render(<AflowManagement status={status} issues={page} error={null} onRefresh={vi.fn(async () => undefined)} />)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Retry local' })).toBeDisabled())
    expect(screen.getByText(/Retry local unavailable: Resubmit from the originating MCP client/)).toBeInTheDocument()
    expect(screen.getByText(/Fireworks unavailable:/)).toHaveTextContent('originating MCP client')
  })
})
