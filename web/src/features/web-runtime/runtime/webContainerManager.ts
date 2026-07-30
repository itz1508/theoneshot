/**
 * webContainerManager — module-level singleton owning the WebContainer
 * session so it survives React unmounts (feature switches).
 *
 * Honesty contract: every state transition reflects a real event from the
 * container; failures surface with real error text and remediation.
 * `@webcontainer/api` is loaded via dynamic import at boot time only, so
 * no WebContainer code enters the entry bundle or runs before the user
 * clicks "Boot runtime".
 */

import type { FileSystemTree, WebContainer, WebContainerProcess } from '@webcontainer/api'
import { useWebRuntimeStore } from '../store'
import type { TerminalSource } from '../types'
import { sampleProjectTree } from '../sampleProject'
import { directoryHandleToTree } from './fileTree'
import type { DirectoryHandleLike } from '../types'

type WebContainerModule = { WebContainer: { boot(): Promise<WebContainer> } }

/** Injectable for tests; defaults to the real dynamic import. */
export type ModuleLoader = () => Promise<WebContainerModule>

const defaultLoader: ModuleLoader = () => import('@webcontainer/api')

const ANSI_PATTERN = /\x1b\[[0-9;?]*[a-zA-Z]/g

export function validateBackendUrl(
  value: string,
): { ok: true; url: string | null } | { ok: false; reason: string } {
  const trimmed = value.trim()
  if (!trimmed) return { ok: true, url: null }
  let parsed: URL
  try {
    parsed = new URL(trimmed)
  } catch {
    return { ok: false, reason: 'not an absolute URL' }
  }
  if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
    return { ok: false, reason: 'only http(s) URLs are supported' }
  }
  if (parsed.search || parsed.hash) {
    return { ok: false, reason: 'query or fragment is not allowed' }
  }
  return { ok: true, url: trimmed }
}

class WebContainerManager {
  private container: WebContainer | null = null
  private processes: WebContainerProcess[] = []
  private previewSet = false

  private get store() {
    return useWebRuntimeStore.getState()
  }

  private log(source: TerminalSource, text: string, isError = false) {
    for (const line of text.replace(ANSI_PATTERN, '').split('\n')) {
      if (line.trim() !== '') this.store.appendLine(source, line, isError)
    }
  }

  private pipe(process: WebContainerProcess, source: TerminalSource) {
    // Single TTY-merged stream: stdout/stderr cannot be separated via the
    // public API — lines are tagged by process channel instead.
    void process.output.pipeTo(
      new WritableStream({
        write: (chunk) => this.log(source, chunk),
      }),
    )
  }

  /** Boot is click-driven only; re-entrancy guarded for StrictMode/double clicks. */
  async boot(loader: ModuleLoader = defaultLoader): Promise<void> {
    const { status } = this.store
    if (status !== 'idle' && status !== 'stopped' && status !== 'failed') return

    if (!self.crossOriginIsolated) {
      this.store.setStatus(
        'failed',
        'This page is not cross-origin isolated. WebContainers require the response headers ' +
          '"Cross-Origin-Opener-Policy: same-origin" and "Cross-Origin-Embedder-Policy: require-corp" ' +
          '(set by web/vite.config.ts for npm run dev / preview). Open the app via http://localhost, ' +
          'not from file:// or a static server without these headers.',
      )
      return
    }

    const backend = validateBackendUrl(this.store.backendUrl)
    if (!backend.ok) {
      this.store.setStatus('failed', `Backend URL rejected — ${backend.reason}. Clear the field or fix the URL in Settings.`)
      return
    }

    this.store.setStatus('booting')
    this.store.resetPreview()
    this.previewSet = false

    try {
      const source = this.store.projectSource
      let tree: FileSystemTree
      if (source.kind === 'sample') {
        tree = sampleProjectTree
        this.log('system', 'Mounting bundled Sample project (real Vite starter).')
      } else {
        tree = await this.pendingFolderTree!
        this.log('system', `Mounting imported folder "${source.name}".`)
      }

      this.log('system', 'Loading @webcontainer/api…')
      const { WebContainer } = await loader()

      this.log('system', 'Booting WebContainer\u2026')
      this.container = await WebContainer.boot()
      
      // Expose container for tool execution binding (acceptance proof)
      ;(window as any).__webContainer = this.container

      this.container.on('server-ready', (port: number, url: string) => {
        if (this.previewSet) {
          // Later servers (extra ports) are logged only.
          this.log('system', `Additional server ready on port ${port}: ${url}`)
          return
        }
        this.previewSet = true
        this.log('system', `Dev server ready on port ${port}: ${url}`)
        this.store.setPreview(url, new URL(url).origin)
        this.store.setStatus('ready')
      })

      this.log('system', 'Mounting file tree…')
      await this.container.mount(tree)

      if (backend.url) {
        await this.container.fs.writeFile('.env.local', `VITE_ASSISTANT_API_URL=${backend.url}\n`)
        this.log('system', `Configured VITE_ASSISTANT_API_URL=${backend.url}`)
        this.log('system', 'Note: this only affects mounted projects that read the variable; the bundled sample does not.')
        this.log('system', "Reminder: the backend must list this page's preview origin in AUDISOR_CORS_ORIGINS.")
      }

      this.store.setStatus('installing')
      this.log('system', 'Running npm install (in-container, needs network)…')
      const install = await this.container.spawn('npm', ['install'])
      this.processes.push(install)
      this.pipe(install, 'install')
      const installExit = await install.exit
      if (installExit !== 0) {
        this.store.setStatus('failed', `npm install exited with code ${installExit} — see terminal output above.`)
        return
      }

      this.store.setStatus('starting')
      this.log('system', 'Starting dev server (npm run dev)…')
      const dev = await this.container.spawn('npm', ['run', 'dev'])
      this.processes.push(dev)
      this.pipe(dev, 'dev')
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error)
      this.store.setStatus('failed', message)
      this.log('system', `ERROR: ${message}`, true)
    }
  }

  /** Resolved folder tree staged by the picker before boot. */
  private pendingFolderTree: Promise<FileSystemTree> | null = null

  stageFolder(handle: DirectoryHandleLike): void {
    this.pendingFolderTree = directoryHandleToTree(handle)
    this.store.setProjectSource({ kind: 'folder', name: handle.name })
  }

  useSampleProject(): void {
    this.pendingFolderTree = null
    this.store.setProjectSource({ kind: 'sample' })
  }

  async stop(): Promise<void> {
    for (const process of this.processes) {
      try {
        process.kill()
      } catch {
        // process may have already exited
      }
    }
    this.processes = []
    if (this.container) {
      this.container.teardown()
      this.container = null
    }
    this.previewSet = false
    this.store.resetPreview()
    this.store.setStatus('stopped')
    this.log('system', 'Runtime stopped — container torn down. Boot again to restart.')
  }
}

export const webContainerManager = new WebContainerManager()
