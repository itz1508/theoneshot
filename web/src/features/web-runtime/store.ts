/**
 * Web Runtime store — module-level zustand store so runtime state
 * (status, terminal lines, preview URL) survives React unmounts when the
 * user switches to another Audisor feature. The WebContainer session
 * itself lives in the webContainerManager singleton.
 */

import { create } from 'zustand'
import type { ProjectSource, RuntimeStatus, TerminalLine, TerminalSource } from './types'

/** Terminal line cap — oldest lines are trimmed beyond this. */
export const MAX_TERMINAL_LINES = 5000

export const DEFAULT_TERMINAL_HEIGHT = 220
export const MIN_TERMINAL_HEIGHT = 120

let nextLineId = 1

export interface WebRuntimeState {
  status: RuntimeStatus
  failure: string | null
  lines: TerminalLine[]
  previewUrl: string | null
  previewOrigin: string | null
  projectSource: ProjectSource
  backendUrl: string
  terminalHeight: number
  terminalCollapsed: boolean
  previewMaximized: boolean
  settingsOpen: boolean

  setStatus: (status: RuntimeStatus, failure?: string | null) => void
  appendLine: (source: TerminalSource, text: string, isError?: boolean) => void
  clearLines: () => void
  setPreview: (url: string, origin: string) => void
  resetPreview: () => void
  setProjectSource: (source: ProjectSource) => void
  setBackendUrl: (url: string) => void
  setTerminalHeight: (height: number) => void
  toggleTerminalCollapsed: () => void
  setPreviewMaximized: (maximized: boolean) => void
  setSettingsOpen: (open: boolean) => void
}

export const useWebRuntimeStore = create<WebRuntimeState>((set) => ({
  status: 'idle',
  failure: null,
  lines: [],
  previewUrl: null,
  previewOrigin: null,
  projectSource: { kind: 'sample' },
  backendUrl: '',
  terminalHeight: DEFAULT_TERMINAL_HEIGHT,
  terminalCollapsed: false,
  previewMaximized: false,
  settingsOpen: false,

  setStatus: (status, failure = null) => set({ status, failure }),

  appendLine: (source, text, isError = false) =>
    set((s) => {
      let lines = [...s.lines, { id: nextLineId++, source, text, isError }]
      if (lines.length > MAX_TERMINAL_LINES) {
        lines = [
          { id: nextLineId++, source: 'system' as const, text: '… older output trimmed', isError: false },
          ...lines.slice(lines.length - MAX_TERMINAL_LINES),
        ]
      }
      return { lines }
    }),

  clearLines: () => set({ lines: [] }),

  setPreview: (url, origin) => set({ previewUrl: url, previewOrigin: origin }),

  resetPreview: () => set({ previewUrl: null, previewOrigin: null, previewMaximized: false }),

  setProjectSource: (source) => set({ projectSource: source }),

  setBackendUrl: (url) => set({ backendUrl: url }),

  setTerminalHeight: (height) => set({ terminalHeight: height }),

  toggleTerminalCollapsed: () => set((s) => ({ terminalCollapsed: !s.terminalCollapsed })),

  setPreviewMaximized: (maximized) => set({ previewMaximized: maximized }),

  setSettingsOpen: (open) => set({ settingsOpen: open }),
}))
