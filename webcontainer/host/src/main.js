/**
 * main.js — boots the Audisor web app inside a WebContainer.
 *
 * Honesty contract:
 * - the mounted app talks only to the real assistant backend the operator
 *   configures (VITE_ASSISTANT_API_URL written to .env.local before boot);
 * - with no backend configured, the app renders its normalized
 *   "unavailable" state — nothing here fakes or substitutes responses.
 */
import { WebContainer } from '@webcontainer/api'

const logEl = document.getElementById('log')
const bootButton = document.getElementById('boot')
const backendInput = document.getElementById('backend-url')
const previewFrame = document.getElementById('preview')
const placeholder = document.getElementById('placeholder')
const originLine = document.getElementById('origin-line')
const originCode = document.getElementById('preview-origin')

function log(line, isError = false) {
  const span = document.createElement('span')
  if (isError) span.className = 'err'
  span.textContent = line.endsWith('\n') ? line : line + '\n'
  logEl.appendChild(span)
  logEl.scrollTop = logEl.scrollHeight
}

function clearLog() {
  logEl.textContent = ''
}

/** Convert the flat snapshot map into a WebContainer FileSystemTree. */
function toFileSystemTree(files) {
  const tree = {}
  for (const [path, entry] of Object.entries(files)) {
    const segments = path.split('/')
    let node = tree
    for (const dir of segments.slice(0, -1)) {
      node[dir] ??= { directory: {} }
      node = node[dir].directory
    }
    const name = segments[segments.length - 1]
    if (entry.kind === 'binary') {
      const raw = atob(entry.base64)
      const bytes = new Uint8Array(raw.length)
      for (let i = 0; i < raw.length; i += 1) bytes[i] = raw.charCodeAt(i)
      node[name] = { file: { contents: bytes } }
    } else {
      node[name] = { file: { contents: entry.contents } }
    }
  }
  return tree
}

/** Pipe a WebContainer process's output into the log pane. */
function pipeOutput(process) {
  process.output.pipeTo(
    new WritableStream({
      write(chunk) {
        log(chunk.replace(/\x1b\[[0-9;?]*[a-zA-Z]/g, ''))
      },
    }),
  )
}

function validateBackendUrl(value) {
  if (!value) return { ok: true, url: null }
  let parsed
  try {
    parsed = new URL(value)
  } catch {
    return { ok: false, reason: 'not an absolute URL' }
  }
  if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
    return { ok: false, reason: 'only http(s) URLs are supported' }
  }
  if (parsed.search || parsed.hash) {
    return { ok: false, reason: 'query or fragment is not allowed' }
  }
  return { ok: true, url: value }
}

async function boot() {
  bootButton.disabled = true
  backendInput.disabled = true
  clearLog()

  // Hard prerequisite: WebContainers need cross-origin isolation.
  if (!self.crossOriginIsolated) {
    log('ERROR: this page is not cross-origin isolated.', true)
    log('WebContainers require these response headers on the host page:', true)
    log('  Cross-Origin-Opener-Policy: same-origin', true)
    log('  Cross-Origin-Embedder-Policy: require-corp', true)
    log('Serve this app with `npm run dev` (vite.config.js sets both), and', true)
    log('open it via http://localhost — not from file:// or a plain static server.', true)
    return
  }

  const backend = validateBackendUrl(backendInput.value.trim())
  if (!backend.ok) {
    log(`ERROR: backend URL rejected — ${backend.reason}.`, true)
    log('Leave the field empty for the normalized "unavailable" state instead.', true)
    bootButton.disabled = false
    backendInput.disabled = false
    return
  }

  log('Fetching web app snapshot…')
  const response = await fetch('/files.snapshot.json')
  if (!response.ok) {
    log('ERROR: files.snapshot.json not found.', true)
    log('Generate it first: node ../scripts/snapshot.mjs (or `npm run snapshot`).', true)
    bootButton.disabled = false
    backendInput.disabled = false
    return
  }
  const snapshot = await response.json()
  log(`Snapshot: ${snapshot.fileCount} files from ${snapshot.source}/ (${snapshot.generatedAt})`)

  log('Booting WebContainer…')
  const container = await WebContainer.boot()

  log('Mounting file tree…')
  await container.mount(toFileSystemTree(snapshot.files))

  if (backend.url) {
    // Real backend only: the value flows into the app exactly like any
    // other deployment configuration. Written before the dev server starts.
    await container.fs.writeFile('.env.local', `VITE_ASSISTANT_API_URL=${backend.url}\n`)
    log(`Configured VITE_ASSISTANT_API_URL=${backend.url}`)
    log('Reminder: the backend must list this page\'s preview origin in AUDISOR_CORS_ORIGINS.')
  } else {
    log('No backend URL configured — the assistant will honestly render its "unavailable" state.')
  }

  log('Running npm install (in-container, needs network; this can take a few minutes)…')
  const install = container.spawn('npm', ['install'])
  pipeOutput(await install)
  const installExit = await (await install).exit
  if (installExit !== 0) {
    log(`ERROR: npm install exited with code ${installExit}.`, true)
    return
  }

  container.on('server-ready', (port, url) => {
    log(`Dev server ready on port ${port}: ${url}`)
    const origin = new URL(url).origin
    originCode.textContent = origin
    originLine.hidden = false
    placeholder.style.display = 'none'
    previewFrame.hidden = false
    previewFrame.src = url
  })

  log('Starting vite dev server…')
  pipeOutput(await container.spawn('npm', ['run', 'dev']))
}

bootButton.addEventListener('click', () => {
  boot().catch((error) => {
    log(`ERROR: ${error?.message ?? error}`, true)
    bootButton.disabled = false
    backendInput.disabled = false
  })
})
