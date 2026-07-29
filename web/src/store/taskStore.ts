/**
 * Central task store — single source of truth for all UI state.
 *
 * Consumes events from TaskEventSource.
 * Explorer, LED, chat, drawer, tracing line, participant ownership,
 * live activity, and persistent Task Record all derive from here.
 */

import { create } from 'zustand'
import type {
  AgentEvent,
  ChatMessage,
  MessageTokenUsage,
  ActivityUpdate,
  Stage,
  TaskActivity,
  TaskState,
  TaskStatus,
  ParticipantId,
  ActivityStatus,
  Workspace,
} from '../agent/types'
import type { TaskEventSource } from '../agent/TaskEventSource'
import {
  capacityFromEstimate,
  fetchChatEstimate,
  initialCapacity,
} from '../agent/chatCapacity'
import type { ChatCapacity, ChatHistoryEntry } from '../agent/chatCapacity'
import { validateRecordEntry } from '../agent/validation'
import type { ValidationFailure } from '../agent/validation'

// ─── Store state ───

export interface AppState {
  // Workspaces
  workspaces: Workspace[]
  participatingWorkspaceIds: string[]

  // Task
  task: TaskState
  drawerOpen: boolean

  // Chat
  messages: ChatMessage[]
  loading: boolean

  // Composer draft + live capacity meter (single shared owner for
  // draft text, history, selected model, and context metadata)
  draft: string
  selectedModel: string | null
  capacity: ChatCapacity

  // Turn manager — strict alternation: user goes first, agent responds,
  // then control returns to the user. Out-of-turn sends are ignored.
  turn: 'user' | 'agent'

  // Connection
  runnerMode: string

  // Validation
  lastValidationFailure: ValidationFailure | null

  // Actions
  setWorkspaces: (ws: Workspace[]) => void
  addWorkspace: (ws: Workspace) => void
  removeWorkspace: (id: string) => void
  setDraft: (text: string) => void
  setSelectedModel: (model: string | null) => void
  requestEstimateNow: () => void
  _scheduleEstimate: () => void
  sendMessage: (text: string) => void
  cancelTask: () => void
  toggleDrawer: () => void
  openDrawerForWorkspace: (workspaceId: string) => void
  handleEvent: (event: AgentEvent) => void
  reset: () => void

  // Event source binding (set once at app boot)
  _eventSource: TaskEventSource | null
  _unsubscribe: (() => void) | null
  bindEventSource: (source: TaskEventSource) => void
}

// ─── Initial workspace data (no Hackathon) ───

const initialWorkspaces: Workspace[] = [
  {
    id: 'ws-theoneshot',
    name: 'Theoneshot',
    stage: 'idle',
    isActive: false,
    taskParticipant: false,
    files: [
      { id: 'f1', name: 'web', type: 'folder', children: [
        { id: 'f1-1', name: 'src', type: 'folder', children: [
          { id: 'f1-1-1', name: 'components', type: 'folder', children: [
            { id: 'f1-1-1-1', name: 'Explorer.tsx', type: 'file' },
            { id: 'f1-1-1-2', name: 'ActivityLED.tsx', type: 'file' },
            { id: 'f1-1-1-3', name: 'Conversation.tsx', type: 'file' },
          ]},
          { id: 'f1-1-2', name: 'App.tsx', type: 'file' },
          { id: 'f1-1-3', name: 'main.tsx', type: 'file' },
        ]},
        { id: 'f1-2', name: 'package.json', type: 'file' },
      ]},
      { id: 'f2', name: 'README.md', type: 'file' },
    ],
  },
  {
    id: 'ws-audisor',
    name: 'Audisor Toolkit',
    stage: 'idle',
    isActive: false,
    taskParticipant: false,
    files: [
      { id: 'f3', name: 'backend', type: 'folder', children: [
        { id: 'f3-1', name: 'src', type: 'folder', children: [
          { id: 'f3-1-1', name: 'mcp_server.py', type: 'file' },
          { id: 'f3-1-2', name: 'inspection.py', type: 'file' },
          { id: 'f3-1-3', name: 'contracts.py', type: 'file' },
        ]},
        { id: 'f3-2', name: 'pyproject.toml', type: 'file' },
      ]},
      { id: 'f4', name: 'README.md', type: 'file' },
    ],
  },
  {
    id: 'ws-aflow',
    name: 'A-Flow',
    stage: 'idle',
    isActive: false,
    taskParticipant: false,
    files: [
      { id: 'f5', name: 'src', type: 'folder', children: [
        { id: 'f5-1', name: 'engine.py', type: 'file' },
        { id: 'f5-2', name: 'schemas.py', type: 'file' },
      ]},
      { id: 'f6', name: 'pyproject.toml', type: 'file' },
    ],
  },
]

export const initialTask: TaskState = {
  taskId: null,
  status: 'idle',
  objective: '',
  currentWorkspace: '',
  currentStage: 'idle',
  currentFile: '',
  currentAction: '',
  steps: [],
  validationStatus: '',
  filesTouched: [],
  participatingWorkspaceIds: [],
  activeParticipantId: null,
  activeActivityId: null,
  activeActivity: null,
  taskRecord: [],
}

// ─── Capacity estimation plumbing (module-scope, not reactive state) ───

const ESTIMATE_DEBOUNCE_MS = 300

let estimateTimer: ReturnType<typeof setTimeout> | null = null
let estimateSeq = 0
let estimateAbort: AbortController | null = null

/** The conversation turns that will actually be sent with the next request. */
function historyFromMessages(messages: ChatMessage[]): ChatHistoryEntry[] {
  return messages
    .filter((m) => m.content.trim().length > 0)
    .map((m) => ({
      role: m.role === 'user' ? ('user' as const) : ('assistant' as const),
      content: m.content,
    }))
}

// ─── Store ───

export const useAppStore = create<AppState>((set, get) => ({
  workspaces: initialWorkspaces,
  participatingWorkspaceIds: [],
  task: initialTask,
  drawerOpen: false,
  messages: [],
  loading: false,
  draft: '',
  selectedModel: null,
  capacity: initialCapacity,
  turn: 'user',
  runnerMode: 'Connected · assistant backend',
  lastValidationFailure: null,
  _eventSource: null,
  _unsubscribe: null,

  setWorkspaces: (ws) => set({ workspaces: ws }),

  addWorkspace: (ws) => set((s) => ({ workspaces: [...s.workspaces, ws] })),

  removeWorkspace: (id) => set((s) => ({
    workspaces: s.workspaces.filter((w) => w.id !== id),
  })),

  setDraft: (text) => {
    set({ draft: text })
    get()._scheduleEstimate()
  },

  setSelectedModel: (model) => {
    set({ selectedModel: model })
    // Model change moves the allowance immediately — no debounce wait
    get().requestEstimateNow()
  },

  _scheduleEstimate: () => {
    if (estimateTimer != null) clearTimeout(estimateTimer)
    estimateTimer = setTimeout(() => {
      estimateTimer = null
      get().requestEstimateNow()
    }, ESTIMATE_DEBOUNCE_MS)
  },

  requestEstimateNow: () => {
    // Sequence + abort guard: a stale estimate can never overwrite a
    // newer one, and superseded in-flight requests are cancelled.
    const seq = ++estimateSeq
    estimateAbort?.abort()
    const abort = new AbortController()
    estimateAbort = abort

    const { draft, selectedModel, messages } = get()
    set((s) => ({ capacity: { ...s.capacity, status: 'estimating' as const } }))

    fetchChatEstimate(
      {
        message: draft,
        ...(selectedModel ? { model: selectedModel } : {}),
        history: historyFromMessages(messages),
      },
      abort.signal,
    )
      .then((estimate) => {
        if (seq !== estimateSeq) return // stale — a newer request owns the meter
        set({ capacity: capacityFromEstimate(estimate) })
      })
      .catch(() => {
        if (seq !== estimateSeq || abort.signal.aborted) return
        // Backend unavailable: explicit unavailable state — never
        // fabricated numbers, never demo data.
        set({ capacity: { ...initialCapacity, status: 'unavailable' as const } })
      })
  },

  sendMessage: (text) => {
    const { _eventSource, workspaces, turn, loading, capacity, selectedModel, messages } = get()
    if (!_eventSource || !text.trim()) return
    // Turn manager: ignore out-of-turn submissions while the agent responds
    if (turn !== 'user' || loading) return
    // Over-limit guard: the estimated request cannot fit the usable
    // allowance — the composer explains this instead of submitting.
    if (capacity.overLimit) return

    const userMsg: ChatMessage = {
      id: `msg-${Date.now()}`,
      role: 'user',
      content: text.trim(),
      timestamp: new Date().toISOString(),
    }

    const primary = workspaces[0]?.id ?? ''
    const linked = workspaces.slice(1).map((w) => w.id)

    const taskId = _eventSource.start(text, primary, linked, {
      history: historyFromMessages(messages),
      ...(selectedModel ? { model: selectedModel } : {}),
    })

    set((s) => ({
      messages: [...s.messages, userMsg],
      draft: '',
      loading: true,
      turn: 'agent',
      drawerOpen: true,
      task: {
        ...initialTask,
        taskId,
        status: 'queued',
        objective: text.trim(),
        participatingWorkspaceIds: [primary, ...linked],
      },
      participatingWorkspaceIds: [primary, ...linked],
      workspaces: s.workspaces.map((ws) => ({
        ...ws,
        taskParticipant: ws.id === primary || linked.includes(ws.id),
        stage: 'idle' as Stage,
        isActive: false,
      })),
    }))
    // Draft cleared and history grew — refresh the capacity meter
    get()._scheduleEstimate()
  },

  cancelTask: () => {
    const { _eventSource } = get()
    _eventSource?.cancel()
  },

  toggleDrawer: () => set((s) => ({ drawerOpen: !s.drawerOpen })),

  openDrawerForWorkspace: (workspaceId) => {
    const ws = get().workspaces.find((w) => w.id === workspaceId)
    if (ws) {
      set((s) => ({
        drawerOpen: true,
        task: { ...s.task, currentWorkspace: ws.name },
      }))
    }
  },

  handleEvent: (event) => {
    const isTerminalEvent = event.eventType === 'task.completed' ||
      event.eventType === 'task.failed' ||
      event.eventType === 'task.cancelled'

    set((s) => {
      const isTerminal = event.eventType === 'task.completed' ||
        event.eventType === 'task.failed' ||
        event.eventType === 'task.cancelled'

      // ─── Participant ownership events ───
      if (event.eventType === 'participant.activated') {
        const participantId = (event.metadata?.participantId as ParticipantId) ?? 'audisor'
        const activityId = (event.metadata?.activityId as string) ?? `act-${Date.now()}`
        const summary = event.message
        const newActivity: TaskActivity = {
          activityId,
          participantId,
          status: 'working' as ActivityStatus,
          summary,
          messages: [],
        }
        return {
          task: {
            ...s.task,
            status: 'running' as TaskStatus,
            activeParticipantId: participantId,
            activeActivityId: activityId,
            activeActivity: newActivity,
            currentAction: summary,
          },
          loading: true,
        }
      }

      if (event.eventType === 'participant.activity_update') {
        const activity = s.task.activeActivity
        if (!activity) return {}
        const msg: ActivityUpdate = {
          id: event.eventId,
          text: event.message,
          timestamp: event.timestamp,
        }
        return {
          task: {
            ...s.task,
            activeActivity: {
              ...activity,
              messages: [...activity.messages, msg],
              summary: event.message,
            },
            currentAction: event.message,
          },
        }
      }

      if (event.eventType === 'participant.outcome_recorded') {
        const payload = event.metadata?.entry
        const validation = validateRecordEntry(payload)
        if (!validation.valid) {
          // Reject: do not append, do not clear activity
          return { lastValidationFailure: validation }
        }
        return {
          lastValidationFailure: null,
          task: {
            ...s.task,
            activeActivity: null,
            activeActivityId: null,
            activeParticipantId: null,
            taskRecord: [...s.task.taskRecord, validation.entry],
          },
        }
      }

      // ─── Workspace and general events ───
      const workspaces = s.workspaces.map((ws) => {
        if (ws.id === event.workspaceId) {
          return { ...ws, stage: event.stage, isActive: !isTerminal }
        }
        if (ws.isActive && event.workspaceId && ws.id !== event.workspaceId) {
          return { ...ws, isActive: false }
        }
        if (isTerminal && ws.isActive) {
          return { ...ws, isActive: false, stage: 'completed' as Stage }
        }
        return ws
      })

      const task: TaskState = {
        ...s.task,
        status: isTerminal ? (event.stage === 'cancelled' ? 'cancelled' : 'completed') as TaskStatus : 'running',
        currentStage: event.stage,
        currentWorkspace: workspaces.find((w) => w.id === event.workspaceId)?.name ?? s.task.currentWorkspace,
        currentFile: event.filePath ?? s.task.currentFile,
        currentAction: event.message,
        filesTouched: event.filePath && !s.task.filesTouched.includes(event.filePath)
          ? [...s.task.filesTouched, event.filePath]
          : s.task.filesTouched,
        validationStatus: event.eventType === 'validation.completed' ? 'Passed' : s.task.validationStatus,
      }

      // Terminal events clear participant ownership
      if (isTerminal) {
        task.activeParticipantId = null
        task.activeActivityId = null
        task.activeActivity = null
      }

      let messages = s.messages
      if (isTerminal) {
        // Token usage only when the backend provides real data.
        // No fake/approximated values — only real provider-reported counts.
        const hasRealTokens = typeof event.metadata?.inputTokens === 'number'
        const tokens: MessageTokenUsage | undefined = hasRealTokens
          ? {
              input_tokens: event.metadata!.inputTokens as number,
              output_tokens: event.metadata!.outputTokens as number,
              total_tokens: event.metadata!.totalTokens as number,
              cost: (event.metadata!.cost as number | null) ?? null,
              provider: (event.metadata!.tokenProvider as 'local' | 'cloud') ?? 'local',
            }
          : undefined

        const agentMsg: ChatMessage = {
          id: `msg-${Date.now()}-agent`,
          role: 'agent',
          content: event.message,
          timestamp: event.timestamp ?? new Date().toISOString(),
          activities: [{
            id: `act-${Date.now()}`,
            label: 'Task result',
            detail: `Stage: ${event.stage}. Files touched: ${task.filesTouched.join(', ') || 'none'}`,
            status: 'completed',
          }],
          ...(tokens && { tokens }),
        }
        messages = [...messages, agentMsg]
      }

      return {
        workspaces,
        task,
        messages,
        loading: !isTerminal,
        // Agent turn ends once its response lands in the thread
        turn: isTerminal ? ('user' as const) : s.turn,
      }
    })

    // Terminal events change the history sent with the next request
    if (isTerminalEvent) {
      get()._scheduleEstimate()
    }
  },

  reset: () => {
    if (estimateTimer != null) {
      clearTimeout(estimateTimer)
      estimateTimer = null
    }
    estimateAbort?.abort()
    estimateAbort = null
    estimateSeq += 1 // invalidate any in-flight estimate
    set({
      workspaces: initialWorkspaces,
      participatingWorkspaceIds: [],
      task: initialTask,
      drawerOpen: false,
      messages: [],
      loading: false,
      draft: '',
      selectedModel: null,
      capacity: initialCapacity,
      turn: 'user',
      lastValidationFailure: null,
    })
  },

  bindEventSource: (source) => {
    const prev = get()._unsubscribe
    if (prev) prev()

    const unsub = source.subscribe((event) => {
      get().handleEvent(event)
    })

    set({ _eventSource: source, _unsubscribe: unsub })
  },
}))
