/**
 * WebRuntime — feature root.
 *
 * Kills the recursion: this panel runs the SELECTED PROJECT inside a
 * WebContainer and previews it — it never embeds another Audisor shell.
 * The component is mounted persistently (CSS-hidden) by App.tsx so the
 * container session, iframe, and terminal history survive feature
 * switches; runtime state lives in the module-level store + manager.
 * Nothing boots until the user clicks "Boot runtime".
 */

import { ExternalLink, Minimize2, Settings, Square } from 'lucide-react'
import { useWebRuntimeStore } from './store'
import { webContainerManager } from './runtime/webContainerManager'
import type { RuntimeStatus } from './types'
import { BootScreen } from './components/BootScreen'
import { TerminalPane } from './components/TerminalPane'
import { PreviewPane } from './components/PreviewPane'
import { SettingsDrawer } from './components/SettingsDrawer'
import styles from './WebRuntime.module.css'

const BADGE_CLASS: Record<RuntimeStatus, string> = {
  idle: styles.badgeIdle,
  booting: styles.badgeBooting,
  installing: styles.badgeInstalling,
  starting: styles.badgeStarting,
  ready: styles.badgeReady,
  failed: styles.badgeFailed,
  stopped: styles.badgeStopped,
}

export function WebRuntime() {
  const status = useWebRuntimeStore((s) => s.status)
  const projectSource = useWebRuntimeStore((s) => s.projectSource)
  const previewMaximized = useWebRuntimeStore((s) => s.previewMaximized)
  const setPreviewMaximized = useWebRuntimeStore((s) => s.setPreviewMaximized)
  const setSettingsOpen = useWebRuntimeStore((s) => s.setSettingsOpen)

  // Pre-boot states show the BootScreen; a session (even a failing
  // install) shows terminal + preview so its output stays visible.
  const preBoot = status === 'idle' || status === 'stopped' || status === 'failed'
  const sourceLabel =
    projectSource.kind === 'sample' ? 'Sample project' : `Folder: ${projectSource.name}`

  // Single tree: toggling previewMaximized must not move PreviewPane to a
  // different parent, or React remounts the iframe and reloads the session.
  return (
    <div className={styles.panel}>
      {previewMaximized ? (
        <button
          type="button"
          className={styles.restoreButton}
          onClick={() => setPreviewMaximized(false)}
        >
          <Minimize2 size={14} aria-hidden="true" />
          Restore
        </button>
      ) : (
        <div className={styles.toolbar}>
          <span className={`${styles.badge} ${BADGE_CLASS[status]}`}>
            <span className={styles.badgeDot} aria-hidden="true" />
            {status}
          </span>
          <span className={styles.sourceLabel}>{sourceLabel}</span>
          <span className={styles.spacer} />
          {!preBoot ? (
            <button
              type="button"
              className={styles.toolbarButton}
              onClick={() => void webContainerManager.stop()}
            >
              <Square size={14} aria-hidden="true" />
              Stop runtime
            </button>
          ) : null}
          <button
            type="button"
            className={styles.toolbarButton}
            disabled={status !== 'ready'}
            onClick={() => setPreviewMaximized(true)}
          >
            <ExternalLink size={14} aria-hidden="true" />
            Open preview
          </button>
          <button
            type="button"
            className={styles.toolbarButton}
            aria-label="Runtime settings"
            onClick={() => setSettingsOpen(true)}
          >
            <Settings size={14} aria-hidden="true" />
          </button>
        </div>
      )}

      <div className={styles.content}>
        {preBoot ? (
          <BootScreen />
        ) : (
          <>
            <PreviewPane />
            {!previewMaximized ? <TerminalPane /> : null}
          </>
        )}
      </div>

      <SettingsDrawer />
    </div>
  )
}
