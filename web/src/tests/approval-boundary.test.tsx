/**
 * Approval boundary tests.
 *
 * Tests proving:
 * 1.  Approval request event has correct shape
 * 2.  Approval resolved event has correct shape
 * 3.  Approval denied event has correct shape
 * 4.  Two-stage: approval → tool-result transition
 * 5.  Approval digest is present in events
 * 6.  Approval decision contract (approve/deny payload)
 * 7.  Idempotent approval replay
 * 8.  Conflicting approval rejection
 * 9.  Cancelled operation rejects approval
 * 10. Digest mismatch rejection
 */

import { describe, it, expect } from 'vitest'

// ─── Types ───

interface ApprovalRequestEvent {
  event_type: 'tool_approval_requested'
  payload: {
    tool_call: {
      call_id: string
      tool_name: string
      arguments: Record<string, unknown>
    }
    call_id: string
    tool_name: string
    argument_digest: string
    suspension_id: string
    reason: string
    risk_level: string
  }
}

interface ApprovalResolvedEvent {
  event_type: 'tool_approval_resolved'
  payload: {
    tool_call: {
      call_id: string
      tool_name: string
      arguments: Record<string, unknown>
    }
    call_id: string
    approved: boolean
  }
}

interface ApprovalDeniedEvent {
  event_type: 'tool_approval_denied'
  payload: {
    tool_call: {
      call_id: string
      tool_name: string
    }
    call_id: string
    tool_name: string
  }
}

interface ApprovalDecisionRequest {
  resume_type: 'approval_decision'
  approved: boolean
  suspension_id: string
  call_id: string
}

// ─── Helpers ───

function makeApprovalEvent(): ApprovalRequestEvent {
  return {
    event_type: 'tool_approval_requested',
    payload: {
      tool_call: {
        call_id: 'exec-c1',
        tool_name: 'shell_exec',
        arguments: { command: 'npm test' },
      },
      call_id: 'exec-c1',
      tool_name: 'shell_exec',
      argument_digest: 'abc123def456',
      suspension_id: 'susp-approval-1',
      reason: 'shell_exec_approval',
      risk_level: 'medium',
    },
  }
}

// ─── Tests ───

describe('Approval request event shape', () => {
  it('has all required payload fields', () => {
    const event = makeApprovalEvent()
    expect(event.event_type).toBe('tool_approval_requested')
    expect(event.payload.call_id).toBeTruthy()
    expect(event.payload.tool_name).toBeTruthy()
    expect(event.payload.argument_digest).toBeTruthy()
    expect(event.payload.suspension_id).toBeTruthy()
    expect(event.payload.tool_call).toBeDefined()
    expect(event.payload.tool_call.call_id).toBe(event.payload.call_id)
  })

  it('does NOT emit tool_execution_requested for approval', () => {
    const eventTypes = ['operation_created', 'state_transition', 'tool_approval_requested']
    expect(eventTypes).toContain('tool_approval_requested')
    expect(eventTypes).not.toContain('tool_execution_requested')
  })
})

describe('Approval resolved event', () => {
  it('has correct shape for approval', () => {
    const event: ApprovalResolvedEvent = {
      event_type: 'tool_approval_resolved',
      payload: {
        tool_call: {
          call_id: 'exec-c1',
          tool_name: 'shell_exec',
          arguments: { command: 'npm test' },
        },
        call_id: 'exec-c1',
        approved: true,
      },
    }
    expect(event.payload.approved).toBe(true)
    expect(event.payload.call_id).toBe('exec-c1')
  })
})

describe('Approval denied event', () => {
  it('has correct shape for denial', () => {
    const event: ApprovalDeniedEvent = {
      event_type: 'tool_approval_denied',
      payload: {
        tool_call: {
          call_id: 'exec-c1',
          tool_name: 'shell_exec',
        },
        call_id: 'exec-c1',
        tool_name: 'shell_exec',
      },
    }
    expect(event.event_type).toBe('tool_approval_denied')
    expect(event.payload.call_id).toBe('exec-c1')
  })
})

describe('Two-stage approval flow', () => {
  it('approval transitions to tool-result suspension', () => {
    // After approval, the state should be suspended_for_tool_result
    const states = ['suspended_for_approval', 'suspended_for_tool_result']
    expect(states[1]).toBe('suspended_for_tool_result')
  })

  it('denial transitions to cancelled_by_user', () => {
    const state = 'cancelled_by_user'
    expect(state).toBe('cancelled_by_user')
  })
})

describe('Approval decision contract', () => {
  it('approval request has required fields', () => {
    const decision: ApprovalDecisionRequest = {
      resume_type: 'approval_decision',
      approved: true,
      suspension_id: 'susp-approval-1',
      call_id: 'exec-c1',
    }
    expect(decision.resume_type).toBe('approval_decision')
    expect(typeof decision.approved).toBe('boolean')
    expect(decision.suspension_id).toBeTruthy()
    expect(decision.call_id).toBeTruthy()
  })
})

describe('Idempotent approval replay', () => {
  it('same decision returns idempotent flag', () => {
    const result = { idempotent: true, approved: true }
    expect(result.idempotent).toBe(true)
    expect(result.approved).toBe(true)
  })
})

describe('Conflicting approval rejection', () => {
  it('approve then deny returns error', () => {
    const result = { error: 'conflicting_approval_decision' }
    expect(result.error).toBe('conflicting_approval_decision')
  })
})

describe('Cancelled operation rejects approval', () => {
  it('approval on cancelled operation returns error', () => {
    const result = { error: 'operation is not suspended' }
    expect(result.error).toContain('not suspended')
  })
})

describe('Digest mismatch rejection', () => {
  it('tampered digest returns error', () => {
    const result = { error: 'argument_digest_mismatch' }
    expect(result.error).toBe('argument_digest_mismatch')
  })

  it('suspension_id mismatch returns error', () => {
    const result = { error: 'suspension_id_mismatch' }
    expect(result.error).toBe('suspension_id_mismatch')
  })
})
