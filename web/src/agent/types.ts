/**
 * Shared event and workspace types for the Audisor agent layer.
 *
 * This file is the canonical source of truth for event shapes,
 * workspace models, task state, and LED state mapping.
 */

// ─── LED states (5 total) ───

export type LedState = 'idle' | 'working' | 'waiting' | 'blocked' | 'completed'

// ─── Stage labels (displayed beside the LED) ───

export type Stage =
  | 'idle'
  | 'queued'
  | 'reading'
  | 'planning'
  | 'reviewing'
  | 'editing'
  | 'testing'
  | 'waiting'
  | 'blocked'
  | 'completed'
  | 'cancelled'

/** Map any stage to its 5-state LED visual */
export function stageToLed(stage: Stage): LedState {
  switch (stage) {
    case 'idle':
      return 'idle'
    case 'queued':
    case 'reading':
    case 'planning':
    case 'reviewing':
    case 'editing':
    case 'testing':
      return 'working'
    case 'waiting':
      return 'waiting'
    case 'blocked':
      return 'blocked'
    case 'completed':
    case 'cancelled':
      return 'completed'
  }
}

/** Human-readable label for a stage */
export function stageLabel(stage: Stage): string {
  const labels: Record<Stage, string> = {
    idle: 'Idle',
    queued: 'Queued',
    reading: 'Reading',
    planning: 'Planning',
    reviewing: 'Reviewing',
    editing: 'Editing',
    testing: 'Testing',
    waiting: 'Waiting',
    blocked: 'Blocked',
    completed: 'Completed',
    cancelled: 'Cancelled',
  }
  return labels[stage]
}

// ─── Participant ownership ───

export type ParticipantId = 'audisor' | 'aflow' | (string & {})

export type ActivityStatus =
  | 'idle'
  | 'working'
  | 'waiting'
  | 'blocked'
  | 'completed'
  | 'failed'
  | 'cancelled'

export interface ActivityUpdate {
  id: string
  text: string
  timestamp: string
}

export interface TaskActivity {
  activityId: string
  participantId: ParticipantId
  status: ActivityStatus
  summary: string
  messages: ActivityUpdate[]
}

// ─── Task Record Entry (discriminated union) ───

export interface RootCause {
  summary: string
  evidence: string[]
}

export interface Resolution {
  summary: string
  action: string
  status: 'proposed' | 'applied' | 'verified'
  evidence: string[]
}

interface RecordEntryBase {
  entryId: string
  activityId: string
  participantId: ParticipantId
  title: string
  outcome: string
  evidence: string[]
  artifacts: string[]
  materialGaps: string[]
  nextAuthorisedAction: string | null
  timestamp: string
}

export type SuccessfulRecordEntry = RecordEntryBase & {
  status: 'completed'
  rootCause?: never
  resolution?: never
}

export type CorrectionRecordEntry = RecordEntryBase & {
  status: 'correction_required'
  rootCause: RootCause
  resolution: Resolution
}

export type FailedRecordEntry = RecordEntryBase & {
  status: 'failed'
  rootCause: RootCause
  resolution: Resolution
}

export type BlockedRecordEntry = RecordEntryBase & {
  status: 'blocked'
  blockingReason: string
}

export type CancelledRecordEntry = RecordEntryBase & {
  status: 'cancelled'
  cancellationReason: string
}

export type TaskRecordEntry =
  | SuccessfulRecordEntry
  | CorrectionRecordEntry
  | FailedRecordEntry
  | BlockedRecordEntry
  | CancelledRecordEntry

// ─── Event types ───

export type EventType =
  | 'task.created'
  | 'message.received'
  | 'stage.changed'
  | 'workspace.entered'
  | 'workspace.touched'
  | 'file.read'
  | 'file.changed'
  | 'validation.started'
  | 'validation.completed'
  | 'task.cancelled'
  | 'task.failed'
  | 'task.completed'
  | 'participant.activated'
  | 'participant.activity_update'
  | 'participant.outcome_recorded'

// ─── Agent Event ───

export interface AgentEvent {
  eventId: string
  sequence: number
  taskId: string
  eventType: EventType
  stage: Stage
  workspaceId?: string
  filePath?: string
  message: string
  timestamp: string
  metadata?: Record<string, unknown>
}

// ─── File tree ───

export interface FileNode {
  id: string
  name: string
  type: 'file' | 'folder'
  children?: FileNode[]
}

// ─── Workspace ───

export interface Workspace {
  id: string
  name: string
  stage: Stage
  files: FileNode[]
  /** Part of the active task group */
  taskParticipant: boolean
  /** Currently receiving active events */
  isActive: boolean
}

// ─── Task state ───

export type TaskStatus = 'idle' | 'queued' | 'running' | 'completed' | 'failed' | 'cancelled'

export interface TaskStep {
  id: string
  label: string
  status: 'completed' | 'active' | 'pending'
  workspace: string
  file?: string
  action?: string
}

export interface TaskState {
  taskId: string | null
  status: TaskStatus
  objective: string
  currentWorkspace: string
  currentStage: Stage
  currentFile: string
  currentAction: string
  steps: TaskStep[]
  validationStatus: string
  filesTouched: string[]
  participatingWorkspaceIds: string[]
  // Participant ownership
  activeParticipantId: ParticipantId | null
  activeActivityId: string | null
  activeActivity: TaskActivity | null
  taskRecord: TaskRecordEntry[]
}

// ─── Token usage (per-message) ───

export interface MessageTokenUsage {
  input_tokens: number
  output_tokens: number
  total_tokens: number
  /** null = pricing not applicable (local model); number = cloud cost in USD */
  cost: number | null
  provider: 'local' | 'cloud'
}

// ─── Message ───

export interface ChatMessage {
  id: string
  role: 'user' | 'agent'
  content: string
  timestamp?: string
  activities?: { id: string; label: string; detail: string; status: 'completed' | 'running' | 'pending' }[]
  /** Token usage for this agent turn. Only present on agent messages. */
  tokens?: MessageTokenUsage
}
