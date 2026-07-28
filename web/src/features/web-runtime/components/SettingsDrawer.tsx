/**
 * SettingsDrawer — backend URL + CORS info for the running preview.
 *
 * assistant_backend stays external and independently configured: nothing
 * here talks to it. The URL is only validated and written as `.env.local`
 * inside the container on the NEXT boot.
 */

import { useState } from 'react'
import { Copy, Square, X } from 'lucide-react'
import { useWebRuntimeStore } from '../store'
import { validateBackendUrl, webContainerManager } from '../runtime/webContainerManager'
import styles from '../WebRuntime.module.css'

export function SettingsDrawer() {
  const open = useWebRuntimeStore((s) => s.settingsOpen)
  const setOpen = useWebRuntimeStore((s) => s.setSettingsOpen)
  const backendUrl = useWebRuntimeStore((s) => s.backendUrl)
  const setBackendUrl = useWebRuntimeStore((s) => s.setBackendUrl)
  const previewOrigin = useWebRuntimeStore((s) => s.previewOrigin)
  const status = useWebRuntimeStore((s) => s.status)

  const [draft, setDraft] = useState(backendUrl)
  const [error, setError] = useState<string | null>(null)
  const [applied, setApplied] = useState(false)
  const [originCopied, setOriginCopied] = useState(false)

  if (!open) return null

  const handleApply = () => {
    const result = validateBackendUrl(draft)
    if (!result.ok) {
      setError(result.reason)
      setApplied(false)
      return
    }
    setError(null)
    setBackendUrl(result.url ?? '')
    setApplied(true)
  }

  const handleCopyOrigin = async () => {
    if (!previewOrigin) return
    await navigator.clipboard.writeText(previewOrigin)
    setOriginCopied(true)
    window.setTimeout(() => setOriginCopied(false), 1500)
  }

  const sessionRunning = status === 'installing' || status === 'starting' || status === 'ready'

  return (
    <>
      <div className={styles.drawerOverlay} onClick={() => setOpen(false)} />
      <aside className={styles.drawer} aria-label="Web Runtime settings">
        <div className={styles.drawerHeader}>
          <h3 className={styles.drawerTitle}>Runtime settings</h3>
          <button
            type="button"
            className={styles.terminalAction}
            aria-label="Close settings"
            onClick={() => setOpen(false)}
          >
            <X size={14} aria-hidden="true" />
          </button>
        </div>

        <div className={styles.drawerSection}>
          <label className={styles.drawerLabel} htmlFor="webruntime-backend-url">
            Backend URL
          </label>
          <input
            id="webruntime-backend-url"
            className={styles.drawerInput}
            type="text"
            placeholder="https://backend.example.com (optional)"
            value={draft}
            onChange={(event) => {
              setDraft(event.target.value)
              setApplied(false)
            }}
          />
          {error ? <span className={styles.drawerError}>Rejected — {error}.</span> : null}
          {applied && !error ? <span className={styles.drawerApplied}>Saved — applied on next boot.</span> : null}
          <button type="button" className={styles.toolbarButton} onClick={handleApply}>
            Apply
          </button>
          <p className={styles.drawerHint}>
            Written as VITE_ASSISTANT_API_URL in the container&apos;s .env.local on the next boot
            {sessionRunning ? ' — a running session needs Stop runtime, then Boot runtime' : ''}. Only
            affects mounted projects that read the variable.
          </p>
        </div>

        <div className={styles.drawerSection}>
          <span className={styles.drawerLabel}>CORS — preview origin</span>
          {previewOrigin ? (
            <>
              <div className={styles.originRow}>
                <code className={styles.originCode}>{previewOrigin}</code>
                <button type="button" className={styles.terminalAction} onClick={() => void handleCopyOrigin()}>
                  <Copy size={12} aria-hidden="true" />
                  {originCopied ? 'Copied' : 'Copy'}
                </button>
              </div>
              <p className={styles.drawerHint}>
                Add this exact origin to the backend&apos;s AUDISOR_CORS_ORIGINS for the preview to call
                it. Note: browsers may additionally block requests to private-network backends
                (Private Network Access) — use a backend reachable from the page&apos;s network context.
              </p>
            </>
          ) : (
            <p className={styles.drawerHint}>Available once the dev server is ready.</p>
          )}
        </div>

        <div className={styles.drawerSection}>
          <button
            type="button"
            className={styles.toolbarButton}
            disabled={status === 'idle' || status === 'stopped'}
            onClick={() => void webContainerManager.stop()}
          >
            <Square size={12} aria-hidden="true" />
            Stop runtime
          </button>
        </div>
      </aside>
    </>
  )
}
