/**
 * BackendChatSource — real backend implementation of TaskEventSource.
 *
 * Sends user messages to POST /v1/chat and emits agent events
 * with real provider-reported token usage. Handles the tool-calling
 * loop: 202 responses trigger frontend tool execution or approval
 * dialogs, then continues the turn via POST /v1/chat/continue.
 */

import type {
  AgentEvent, EventType, Stage,
  ToolCallEvent, ToolResultPayload,
  ChatToolCallsPending, ChatApprovalRequired,
  ClaimResponse,
} from './types'
import type { TaskEventSource, EventListener, StartOptions } from './TaskEventSource'
import { FrontendToolExecutor } from './toolExecutor'
import { claimToolExecution, computeArgumentDigest } from '../features/aflow-management/api'

let nextId = 0
function uid(): string {
  return `evt-${Date.now()}-${nextId++}`
}

/** Generate a stable per-tab claimant identifier. */
function generateClaimantId(): string {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) {
    return `tab-${crypto.randomUUID()}`
  }
  return `tab-${Date.now()}-${Math.random().toString(36).slice(2)}`
}

/** Approval resolver — set by the UI component that shows the confirmation. */
export type ApprovalResolver = (approved: boolean) => void

/** Callback invoked when approval is needed. UI should show confirmation. */
export type ApprovalRequestHandler = (
  toolCall: ToolCallEvent,
  reason: string,
  riskLevel: string,
) => Promise<boolean>

export class BackendChatSource implements TaskEventSource {
  private listeners = new Set<EventListener>()
  private abortController: AbortController | null = null
  private currentTaskId: string | null = null
  private toolExecutor = new FrontendToolExecutor()
  private approvalHandler: ApprovalRequestHandler | null = null
  private workspaceAvailable = false
  private claimantId = generateClaimantId()

  /** Bind WebContainer for frontend tool execution. */
  setContainer(container: Parameters<FrontendToolExecutor['setContainer']>[0]): void {
    this.toolExecutor.setContainer(container)
    this.workspaceAvailable = true
  }

  /** Set the approval request handler (called when write/exec needs confirmation). */
  setApprovalHandler(handler: ApprovalRequestHandler): void {
    this.approvalHandler = handler
  }

  start(
    message: string,
    _primaryWorkspaceId: string,
    _linkedWorkspaceIds: string[],
    options?: StartOptions,
  ): string {
    // Cancel any in-flight request
    this.abortController?.abort()

    const taskId = `task-${Date.now()}`
    this.currentTaskId = taskId
    this.abortController = new AbortController()

    // Fire async — start() returns taskId synchronously per interface contract
    this._dispatch(taskId, message, this.abortController.signal, options)

    return taskId
  }

  subscribe(listener: EventListener): () => void {
    this.listeners.add(listener)
    return () => { this.listeners.delete(listener) }
  }

  cancel(): void {
    if (!this.abortController) return
    this.abortController.abort()
    this.abortController = null

    if (this.currentTaskId) {
      this.emit({
        eventId: uid(),
        sequence: 999,
        taskId: this.currentTaskId,
        eventType: 'task.cancelled',
        stage: 'cancelled',
        message: 'Task cancelled by operator',
        timestamp: new Date().toISOString(),
      })
    }
    this.currentTaskId = null
  }

  dispose(): void {
    this.cancel()
    this.listeners.clear()
  }

  private emit(event: AgentEvent): void {
    for (const listener of this.listeners) {
      listener(event)
    }
  }

  private async _dispatch(
    taskId: string,
    message: string,
    signal: AbortSignal,
    options?: StartOptions,
  ): Promise<void> {
    let seq = 0

    // Emit task.created
    this.emit({
      eventId: uid(),
      sequence: seq++,
      taskId,
      eventType: 'task.created',
      stage: 'queued',
      message: 'Sending to backend...',
      timestamp: new Date().toISOString(),
    })

    // Emit participant.activated (agent working)
    this.emit({
      eventId: uid(),
      sequence: seq++,
      taskId,
      eventType: 'participant.activated',
      stage: 'reading',
      message: 'Processing request',
      timestamp: new Date().toISOString(),
      metadata: { participantId: 'audisor', activityId: `act-${Date.now()}` },
    })

    try {
      const response = await fetch('/v1/chat', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-Audisor-Dev-User': 'operator',
        },
        body: JSON.stringify({
          message,
          ...(options?.history?.length ? { history: options.history } : {}),
          ...(options?.model ? { model: options.model } : {}),
          workspace_available: this.workspaceAvailable,
        }),
        signal,
      })

      // Handle the response (may loop for tool calls)
      await this._handleResponse(response, taskId, seq, signal)

    } catch (err: unknown) {
      if (signal.aborted) return // User cancelled, already handled

      const errorMessage = err instanceof Error
        ? `Backend unavailable: ${err.message}`
        : 'Backend unavailable: unknown error'

      this.emit({
        eventId: uid(),
        sequence: seq++,
        taskId,
        eventType: 'task.failed' as EventType,
        stage: 'cancelled' as Stage,
        message: errorMessage,
        timestamp: new Date().toISOString(),
      })
    }
  }

  /**
   * Handle a response from /v1/chat or /v1/chat/continue.
   * Loops on 202 (tool calls pending or approval required).
   */
  private async _handleResponse(
    response: Response,
    taskId: string,
    seq: number,
    signal: AbortSignal,
  ): Promise<void> {
    // Error responses
    if (response.status >= 400) {
      let errorMsg = `Backend error: ${response.status}`
      try {
        const errBody = await response.json()
        if (errBody.error) errorMsg = errBody.error
      } catch { /* use status code */ }

      this.emit({
        eventId: uid(),
        sequence: seq++,
        taskId,
        eventType: 'task.failed' as EventType,
        stage: 'cancelled' as Stage,
        message: errorMsg,
        timestamp: new Date().toISOString(),
      })
      return
    }

    // HTTP 200 — final response (backward-compatible)
    if (response.status === 200) {
      const data = await response.json()
      this._emitToolTrace(data.tool_trace, taskId, seq)
      this.emit({
        eventId: uid(),
        sequence: seq++,
        taskId,
        eventType: 'task.completed',
        stage: 'completed',
        message: data.reply,
        timestamp: new Date().toISOString(),
        metadata: {
          inputTokens: data.usage.input_tokens,
          outputTokens: data.usage.output_tokens,
          totalTokens: data.usage.total_tokens,
          cost: data.usage.cost,
          tokenProvider: data.usage.provider_type,
          model: data.model,
          providerId: data.provider.id,
        },
      })
      return
    }

    // HTTP 202 — tool calls pending or approval required
    if (response.status === 202) {
      const body = await response.json()
      await this._handle202(body, taskId, seq, signal)
      return
    }

    // Unexpected status
    this.emit({
      eventId: uid(),
      sequence: seq++,
      taskId,
      eventType: 'task.failed' as EventType,
      stage: 'cancelled' as Stage,
      message: `Unexpected response status: ${response.status}`,
      timestamp: new Date().toISOString(),
    })
  }

  /**
   * Handle a 202 response body — either tool calls pending or approval required.
   */
  private async _handle202(
    body: ChatToolCallsPending | ChatApprovalRequired,
    taskId: string,
    seq: number,
    signal: AbortSignal,
  ): Promise<void> {
    // Discriminate: ChatToolCallsPending has `pending_calls`, ChatApprovalRequired has `tool_call`
    if ('pending_calls' in body) {
      await this._handleToolCallsPending(body as ChatToolCallsPending, taskId, seq, signal)
    } else if ('tool_call' in body) {
      await this._handleApprovalRequired(body as ChatApprovalRequired, taskId, seq, signal)
    }
  }

  /**
   * Execute pending frontend tool calls and continue the turn.
   */
  private async _handleToolCallsPending(
    pending: ChatToolCallsPending,
    taskId: string,
    seq: number,
    signal: AbortSignal,
  ): Promise<void> {
    // Emit events for completed backend calls
    for (const call of pending.completed_calls) {
      this.emit({
        eventId: uid(),
        sequence: seq++,
        taskId,
        eventType: 'tool.completed',
        stage: 'reading',
        message: `Backend tool completed: ${call.tool_name}`,
        timestamp: new Date().toISOString(),
        metadata: {
          callId: call.call_id,
          toolName: call.tool_name,
          executor: call.executor,
          output: call.output,
          durationMs: call.duration_ms,
        },
      })
    }

    // Emit events for pending frontend calls
    for (const call of pending.pending_calls) {
      this.emit({
        eventId: uid(),
        sequence: seq++,
        taskId,
        eventType: 'tool.call_requested',
        stage: 'editing',
        message: `Tool requested: ${call.tool_name}`,
        timestamp: new Date().toISOString(),
        metadata: {
          callId: call.call_id,
          toolName: call.tool_name,
          arguments: call.arguments,
          executor: call.executor,
        },
      })
    }

    // Set up stream callback for shell output
    this.toolExecutor.setStreamCallback((chunk: string) => {
      this.emit({
        eventId: uid(),
        sequence: seq++,
        taskId,
        eventType: 'tool.output_stream',
        stage: 'editing',
        message: chunk,
        timestamp: new Date().toISOString(),
      })
    })

    // Execute all pending frontend tools
    const results: ToolResultPayload[] = []
    for (const call of pending.pending_calls) {
      if (signal.aborted) break

      this.emit({
        eventId: uid(),
        sequence: seq++,
        taskId,
        eventType: 'tool.executing',
        stage: 'editing',
        message: `Executing: ${call.tool_name}`,
        timestamp: new Date().toISOString(),
        metadata: { callId: call.call_id, toolName: call.tool_name },
      })

      // Claim execution if the call is linked to an operation (refresh-safe)
      const effectiveCall = await this._tryClaim(call, signal)

      const result = await this.toolExecutor.execute(effectiveCall, signal)
      results.push(result)

      const eventType: EventType = result.status === 'success' ? 'tool.completed' : 'tool.failed'
      this.emit({
        eventId: uid(),
        sequence: seq++,
        taskId,
        eventType,
        stage: 'editing',
        message: result.error || `${call.tool_name} completed`,
        timestamp: new Date().toISOString(),
        metadata: {
          callId: call.call_id,
          toolName: call.tool_name,
          output: result.output?.slice(0, 500),
          status: result.status,
        },
      })
    }

    if (signal.aborted) return

    // Continue the turn with results
    const continueResponse = await fetch('/v1/chat/continue', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Audisor-Dev-User': 'operator',
      },
      body: JSON.stringify({
        turn_id: pending.turn_id,
        tool_results: results,
      }),
      signal,
    })

    // Recurse: the continue response may be another 202 or a final 200
    await this._handleResponse(continueResponse, taskId, seq, signal)
  }

  /**
   * Handle an approval-required response — show UI and continue.
   */
  private async _handleApprovalRequired(
    approval: ChatApprovalRequired,
    taskId: string,
    seq: number,
    signal: AbortSignal,
  ): Promise<void> {
    // Emit approval requested event
    this.emit({
      eventId: uid(),
      sequence: seq++,
      taskId,
      eventType: 'tool.approval_requested',
      stage: 'waiting',
      message: approval.reason,
      timestamp: new Date().toISOString(),
      metadata: {
        callId: approval.tool_call.call_id,
        toolName: approval.tool_call.tool_name,
        arguments: approval.tool_call.arguments,
        riskLevel: approval.risk_level,
        turnId: approval.turn_id,
      },
    })

    // Request operator approval
    let approved = false
    if (this.approvalHandler) {
      approved = await this.approvalHandler(
        approval.tool_call,
        approval.reason,
        approval.risk_level,
      )
    }

    // Emit resolution event
    this.emit({
      eventId: uid(),
      sequence: seq++,
      taskId,
      eventType: 'tool.approval_resolved',
      stage: approved ? 'editing' : 'completed',
      message: approved ? 'Approved by operator' : 'Denied by operator',
      timestamp: new Date().toISOString(),
      metadata: {
        callId: approval.tool_call.call_id,
        toolName: approval.tool_call.tool_name,
        approved,
      },
    })

    if (signal.aborted) return

    // Continue with approval/denial result
    const result: ToolResultPayload = {
      call_id: approval.tool_call.call_id,
      tool_name: approval.tool_call.tool_name,
      output: null,
      error: approved ? null : 'Denied by operator',
      status: approved ? 'approved' : 'denied',
    }

    const continueResponse = await fetch('/v1/chat/continue', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Audisor-Dev-User': 'operator',
      },
      body: JSON.stringify({
        turn_id: approval.turn_id,
        tool_results: [result],
      }),
      signal,
    })

    await this._handleResponse(continueResponse, taskId, seq, signal)
  }

  /**
   * Attempt to claim tool execution via the operations claim endpoint.
   * Returns the call to execute (using persisted arguments from the claim
   * response when available). Falls back to the original call when:
   * - The call has no operation_id (not managed by the operations controller)
   * - The claim endpoint returns an error (operation not suspended, etc.)
   * - The request is aborted
   */
  private async _tryClaim(
    call: ToolCallEvent,
    signal: AbortSignal,
  ): Promise<ToolCallEvent> {
    if (!call.operation_id || signal.aborted) return call

    try {
      // Fetch operation status to get suspension_id and argument_digest
      const statusRes = await fetch(
        `/v1/operations/${encodeURIComponent(call.operation_id)}/status`,
        { headers: { 'X-Audisor-Dev-User': 'operator' }, signal },
      )
      if (!statusRes.ok) return call

      const status = await statusRes.json() as {
        state: string
        detail: { suspension_id?: string; argument_digest?: string; pending_calls?: Array<{
          suspension_id: string
          argument_digest: string
        }> }
      }

      // Only claim when the operation is suspended for tool result
      if (status.state !== 'suspended_for_tool_result') return call

      const suspensionId = status.detail.suspension_id
      const expectedDigest = status.detail.argument_digest
      if (!suspensionId) return call

      // Compute the argument digest to verify integrity
      const digest = await computeArgumentDigest(
        call.call_id, call.tool_name, call.arguments,
      )

      const claimResponse: ClaimResponse = await claimToolExecution(
        call.operation_id,
        {
          claimant_id: this.claimantId,
          suspension_id: suspensionId,
          call_id: call.call_id,
          argument_digest: expectedDigest || digest,
        },
      )

      if (!claimResponse.claimed) return call

      // Use the persisted arguments from the claim response, not the
      // claimant-supplied ones — this is the digest invariant
      return {
        ...call,
        arguments: claimResponse.arguments,
      }
    } catch {
      // Claim failed (network error, operation not found, etc.) —
      // fall through to direct execution
      return call
    }
  }

  /**
   * Emit tool trace events from a final response (activity timeline).
   */
  private _emitToolTrace(
    trace: ToolCallEvent[] | null | undefined,
    taskId: string,
    seq: number,
  ): void {
    if (!trace || trace.length === 0) return
    for (const event of trace) {
      this.emit({
        eventId: uid(),
        sequence: seq++,
        taskId,
        eventType: event.status === 'completed' ? 'tool.completed' : 'tool.failed',
        stage: 'reading',
        message: `${event.tool_name}: ${event.output?.slice(0, 200) || event.error || 'done'}`,
        timestamp: new Date().toISOString(),
        metadata: {
          callId: event.call_id,
          toolName: event.tool_name,
          executor: event.executor,
          output: event.output,
          error: event.error,
          durationMs: event.duration_ms,
        },
      })
    }
  }
}
