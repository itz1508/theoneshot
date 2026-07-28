/**
 * web-runtime-ui.test.tsx — BootScreen states, status badges, TerminalPane
 * behavior (render/clear/copy/collapse), store persistence across
 * unmount/remount, and the 5000-line cap.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { WebRuntime } from '../WebRuntime'
import { BootScreen } from '../components/BootScreen'
import { TerminalPane } from '../components/TerminalPane'
import { MAX_TERMINAL_LINES, useWebRuntimeStore } from '../store'
import type { RuntimeStatus } from '../types'

function resetStore() {
  useWebRuntimeStore.setState({
    status: 'idle',
    failure: null,
    lines: [],
    previewUrl: null,
    previewOrigin: null,
    projectSource: { kind: 'sample' },
    backendUrl: '',
    terminalCollapsed: false,
    previewMaximized: false,
    settingsOpen: false,
  })
}

beforeEach(() => {
  resetStore()
  // jsdom has no navigator.clipboard (R10) — stub it explicitly.
  Object.defineProperty(navigator, 'clipboard', {
    value: { writeText: vi.fn().mockResolvedValue(undefined) },
    configurable: true,
  })
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('BootScreen', () => {
  it('disables folder import with an explicit notice when the API is unsupported', () => {
    render(<BootScreen />)
    const folderButton = screen.getByRole('button', { name: /open project folder/i })
    expect(folderButton).toBeDisabled()
    expect(screen.getByText(/not supported in this browser/i)).toBeInTheDocument()
  })

  it('shows the stored failure text in the failed state', () => {
    useWebRuntimeStore.setState({ status: 'failed', failure: 'npm install exited with code 7' })
    render(<BootScreen />)
    expect(screen.getByRole('alert')).toHaveTextContent('npm install exited with code 7')
  })

  it('offers Boot runtime and the privacy note', () => {
    render(<BootScreen />)
    expect(screen.getByRole('button', { name: /boot runtime/i })).toBeInTheDocument()
    expect(screen.getByText(/never uploaded/i)).toBeInTheDocument()
  })
})

describe('WebRuntime status badge', () => {
  const statuses: RuntimeStatus[] = ['idle', 'booting', 'installing', 'starting', 'ready', 'failed', 'stopped']

  it.each(statuses)('shows the %s badge', (status) => {
    useWebRuntimeStore.setState({ status })
    render(<WebRuntime />)
    expect(screen.getByText(status)).toBeInTheDocument()
  })
})

describe('TerminalPane', () => {
  it('renders lines with source distinction and error flag', () => {
    const { appendLine } = useWebRuntimeStore.getState()
    appendLine('system', 'Booting WebContainer…')
    appendLine('install', 'added 12 packages')
    appendLine('dev', 'VITE ready', true)

    render(<TerminalPane />)

    expect(screen.getByText('Booting WebContainer…')).toBeInTheDocument()
    expect(screen.getByText('added 12 packages')).toBeInTheDocument()
    expect(screen.getAllByText('[system]')).toHaveLength(1)
    expect(screen.getAllByText('[install]')).toHaveLength(1)
    expect(screen.getAllByText('[dev]')).toHaveLength(1)
  })

  it('Clear empties the store lines', () => {
    useWebRuntimeStore.getState().appendLine('system', 'something')
    render(<TerminalPane />)
    fireEvent.click(screen.getByRole('button', { name: /clear/i }))
    expect(useWebRuntimeStore.getState().lines).toHaveLength(0)
  })

  it('Copy writes the joined line text to the stubbed clipboard', async () => {
    const { appendLine } = useWebRuntimeStore.getState()
    appendLine('install', 'line one')
    appendLine('dev', 'line two')
    render(<TerminalPane />)

    fireEvent.click(screen.getByRole('button', { name: /copy/i }))

    await waitFor(() =>
      expect(navigator.clipboard.writeText).toHaveBeenCalledWith('line one\nline two'),
    )
  })

  it('collapse toggle hides the output and flips to Expand', () => {
    useWebRuntimeStore.getState().appendLine('system', 'visible line')
    render(<TerminalPane />)

    fireEvent.click(screen.getByRole('button', { name: /collapse/i }))
    expect(screen.queryByText('visible line')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /expand/i }))
    expect(screen.getByText('visible line')).toBeInTheDocument()
  })

  it('retains lines across unmount/remount (module-level store)', () => {
    useWebRuntimeStore.getState().appendLine('dev', 'survives remount')
    const first = render(<TerminalPane />)
    expect(screen.getByText('survives remount')).toBeInTheDocument()
    first.unmount()

    render(<TerminalPane />)
    expect(screen.getByText('survives remount')).toBeInTheDocument()
  })
})

describe('terminal line cap (R3)', () => {
  it(`trims oldest lines beyond ${MAX_TERMINAL_LINES} and inserts a notice`, () => {
    const { appendLine } = useWebRuntimeStore.getState()
    for (let i = 0; i <= MAX_TERMINAL_LINES; i += 1) {
      appendLine('dev', `line-${i}`)
    }

    const { lines } = useWebRuntimeStore.getState()
    expect(lines).toHaveLength(MAX_TERMINAL_LINES + 1)
    expect(lines[0].text).toBe('… older output trimmed')
    expect(lines[0].source).toBe('system')
    expect(lines.some((l) => l.text === 'line-0')).toBe(false)
    expect(lines[lines.length - 1].text).toBe(`line-${MAX_TERMINAL_LINES}`)
  })
})
