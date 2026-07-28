/**
 * runtime-states.test.ts — manager state machine against a mocked
 * WebContainer module (injected via the ModuleLoader DI seam; the real
 * `@webcontainer/api` is never loaded in jsdom).
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useWebRuntimeStore } from '../store'
import { validateBackendUrl, webContainerManager, type ModuleLoader } from '../runtime/webContainerManager'
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
  })
}

function mockProcess(exit: Promise<number>, chunks: string[] = []) {
  return {
    output: new ReadableStream<string>({
      start(controller) {
        for (const chunk of chunks) controller.enqueue(chunk)
        controller.close()
      },
    }),
    exit,
    kill: vi.fn(),
  }
}

function makeContainer({ installExitCode = 0 } = {}) {
  const listeners: Record<string, (...args: unknown[]) => void> = {}
  const install = mockProcess(Promise.resolve(installExitCode), ['added 1 package\n'])
  const dev = mockProcess(new Promise<number>(() => {}), ['VITE ready\n'])
  const container = {
    on: vi.fn((event: string, cb: (...args: unknown[]) => void) => {
      listeners[event] = cb
    }),
    mount: vi.fn().mockResolvedValue(undefined),
    fs: { writeFile: vi.fn().mockResolvedValue(undefined) },
    spawn: vi.fn((_cmd: string, args: string[]) =>
      Promise.resolve(args[0] === 'install' ? install : dev),
    ),
    teardown: vi.fn(),
  }
  const loader = vi.fn(() =>
    Promise.resolve({ WebContainer: { boot: () => Promise.resolve(container) } }),
  ) as unknown as ModuleLoader & ReturnType<typeof vi.fn>
  return { container, listeners, install, dev, loader }
}

function trackStatuses(): { seen: RuntimeStatus[]; unsubscribe: () => void } {
  const seen: RuntimeStatus[] = [useWebRuntimeStore.getState().status]
  const unsubscribe = useWebRuntimeStore.subscribe((s) => {
    if (seen[seen.length - 1] !== s.status) seen.push(s.status)
  })
  return { seen, unsubscribe }
}

beforeEach(() => {
  resetStore()
  vi.stubGlobal('crossOriginIsolated', true)
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('validateBackendUrl', () => {
  it('accepts empty as "no backend"', () => {
    expect(validateBackendUrl('  ')).toEqual({ ok: true, url: null })
  })

  it('accepts absolute http(s) URLs', () => {
    expect(validateBackendUrl('https://api.example.com/v1')).toEqual({
      ok: true,
      url: 'https://api.example.com/v1',
    })
  })

  it('rejects relative URLs, non-http schemes, query and fragment', () => {
    expect(validateBackendUrl('/api').ok).toBe(false)
    expect(validateBackendUrl('ftp://x.example').ok).toBe(false)
    expect(validateBackendUrl('http://x.example/?a=1').ok).toBe(false)
    expect(validateBackendUrl('http://x.example/#frag').ok).toBe(false)
  })
})

describe('boot state machine', () => {
  it('runs idle → booting → installing → starting → ready on the happy path', async () => {
    const { listeners, loader } = makeContainer()
    const { seen, unsubscribe } = trackStatuses()

    await webContainerManager.boot(loader)
    expect(seen).toEqual(['idle', 'booting', 'installing', 'starting'])

    listeners['server-ready'](5173, 'http://localhost:5173')
    expect(useWebRuntimeStore.getState().status).toBe('ready')
    expect(useWebRuntimeStore.getState().previewUrl).toBe('http://localhost:5173')
    expect(useWebRuntimeStore.getState().previewOrigin).toBe('http://localhost:5173')
    unsubscribe()
  })

  it('only the first server-ready sets the preview; later ones are logged', async () => {
    const { listeners, loader } = makeContainer()
    await webContainerManager.boot(loader)

    listeners['server-ready'](5173, 'http://localhost:5173')
    listeners['server-ready'](4321, 'http://localhost:4321')

    const state = useWebRuntimeStore.getState()
    expect(state.previewUrl).toBe('http://localhost:5173')
    expect(state.lines.some((l) => l.text.includes('Additional server ready on port 4321'))).toBe(true)
  })

  it('fails when npm install exits non-zero', async () => {
    const { loader } = makeContainer({ installExitCode: 7 })
    await webContainerManager.boot(loader)

    const state = useWebRuntimeStore.getState()
    expect(state.status).toBe('failed')
    expect(state.failure).toContain('npm install exited with code 7')
  })

  it('fails with the real error message when boot throws', async () => {
    const loader = (() =>
      Promise.resolve({
        WebContainer: { boot: () => Promise.reject(new Error('boot exploded')) },
      })) as unknown as ModuleLoader
    await webContainerManager.boot(loader)

    const state = useWebRuntimeStore.getState()
    expect(state.status).toBe('failed')
    expect(state.failure).toBe('boot exploded')
    expect(state.lines.some((l) => l.isError && l.text.includes('boot exploded'))).toBe(true)
  })

  it('fails with remediation when the page is not cross-origin isolated', async () => {
    vi.stubGlobal('crossOriginIsolated', false)
    const { loader } = makeContainer()
    await webContainerManager.boot(loader)

    const state = useWebRuntimeStore.getState()
    expect(state.status).toBe('failed')
    expect(state.failure).toContain('Cross-Origin-Embedder-Policy')
    expect(loader).not.toHaveBeenCalled()
  })

  it('rejects a malformed backend URL before any container call', async () => {
    useWebRuntimeStore.setState({ backendUrl: 'not a url' })
    const { loader } = makeContainer()
    await webContainerManager.boot(loader)

    const state = useWebRuntimeStore.getState()
    expect(state.status).toBe('failed')
    expect(state.failure).toContain('Backend URL rejected')
    expect(loader).not.toHaveBeenCalled()
  })

  it('is re-entrancy guarded: boot() is a no-op unless idle/stopped/failed', async () => {
    useWebRuntimeStore.setState({ status: 'installing' })
    const { loader } = makeContainer()
    await webContainerManager.boot(loader)
    expect(loader).not.toHaveBeenCalled()
    expect(useWebRuntimeStore.getState().status).toBe('installing')
  })
})

describe('.env.local handling', () => {
  it('writes .env.local only when a valid backend URL is configured', async () => {
    useWebRuntimeStore.setState({ backendUrl: 'https://backend.example.com' })
    const { container, loader } = makeContainer()
    await webContainerManager.boot(loader)

    expect(container.fs.writeFile).toHaveBeenCalledWith(
      '.env.local',
      'VITE_ASSISTANT_API_URL=https://backend.example.com\n',
    )
    // Inert-for-sample note is logged honestly (R9).
    expect(
      useWebRuntimeStore.getState().lines.some((l) => l.text.includes('only affects mounted projects')),
    ).toBe(true)
  })

  it('does not write .env.local when no backend URL is set', async () => {
    const { container, loader } = makeContainer()
    await webContainerManager.boot(loader)
    expect(container.fs.writeFile).not.toHaveBeenCalled()
  })
})

describe('stop', () => {
  it('kills processes, tears down and reaches stopped (reboot allowed)', async () => {
    const { container, install, dev, listeners, loader } = makeContainer()
    await webContainerManager.boot(loader)
    listeners['server-ready'](5173, 'http://localhost:5173')

    await webContainerManager.stop()

    expect(install.kill).toHaveBeenCalled()
    expect(dev.kill).toHaveBeenCalled()
    expect(container.teardown).toHaveBeenCalled()
    const state = useWebRuntimeStore.getState()
    expect(state.status).toBe('stopped')
    expect(state.previewUrl).toBeNull()

    // Reboot is allowed from stopped.
    const second = makeContainer()
    await webContainerManager.boot(second.loader)
    expect(useWebRuntimeStore.getState().status).toBe('starting')
  })
})
