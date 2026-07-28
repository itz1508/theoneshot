/**
 * BootScreen — pre-boot view (idle / stopped / failed). Offers the two
 * honest mount sources: the bundled Sample project (a real Vite starter)
 * or a user folder via the File System Access API. Unsupported browsers
 * see an explicit incapability notice — never a silent fallback.
 */

import { useState } from 'react'
import { FolderOpen, Package, Play } from 'lucide-react'
import { useWebRuntimeStore } from '../store'
import { webContainerManager } from '../runtime/webContainerManager'
import { isDirectoryPickerSupported } from '../runtime/fileTree'
import styles from '../WebRuntime.module.css'

export function BootScreen() {
  const status = useWebRuntimeStore((s) => s.status)
  const failure = useWebRuntimeStore((s) => s.failure)
  const projectSource = useWebRuntimeStore((s) => s.projectSource)
  const [pickError, setPickError] = useState<string | null>(null)

  const pickerSupported = isDirectoryPickerSupported()

  const handlePickFolder = async () => {
    setPickError(null)
    try {
      const handle = await window.showDirectoryPicker!({ mode: 'read' })
      webContainerManager.stageFolder(handle)
    } catch (error) {
      // AbortError means the user cancelled the picker — not an error.
      if (error instanceof DOMException && error.name === 'AbortError') return
      setPickError(error instanceof Error ? error.message : String(error))
    }
  }

  return (
    <div className={styles.bootScreen}>
      <h3 className={styles.bootTitle}>Web Runtime</h3>

      {status === 'failed' && failure ? (
        <div className={styles.bootFailure} role="alert">
          {failure}
        </div>
      ) : null}

      <div className={styles.bootCards}>
        <button
          type="button"
          className={`${styles.sourceCard} ${projectSource.kind === 'sample' ? styles.sourceCardActive : ''}`}
          aria-pressed={projectSource.kind === 'sample'}
          onClick={() => webContainerManager.useSampleProject()}
        >
          <span className={styles.sourceCardLabel}>
            <Package size={14} aria-hidden="true" /> Sample project
          </span>
          <span className={styles.sourceCardDescription}>
            Bundled Vite vanilla starter — a real, buildable project, installed and served
            inside the container.
          </span>
        </button>

        <button
          type="button"
          className={`${styles.sourceCard} ${projectSource.kind === 'folder' ? styles.sourceCardActive : ''}`}
          aria-pressed={projectSource.kind === 'folder'}
          disabled={!pickerSupported}
          onClick={() => void handlePickFolder()}
        >
          <span className={styles.sourceCardLabel}>
            <FolderOpen size={14} aria-hidden="true" /> Open project folder
          </span>
          <span className={styles.sourceCardDescription}>
            {projectSource.kind === 'folder'
              ? `Selected: ${projectSource.name}`
              : 'Mount a local project with a package.json at its root.'}
          </span>
          {!pickerSupported ? (
            <span className={styles.unsupportedNote}>
              Not supported in this browser — folder import needs the File System Access
              API (Chromium-family browsers).
            </span>
          ) : null}
        </button>
      </div>

      {pickError ? (
        <p className={styles.bootError} role="alert">
          {pickError}
        </p>
      ) : null}

      <p className={styles.privacyNote}>
        Files are read into the in-browser container only and never uploaded. The only
        network use is the in-container npm registry install.
      </p>

      <button type="button" className={styles.bootButton} onClick={() => void webContainerManager.boot()}>
        <Play size={14} aria-hidden="true" />
        Boot runtime
      </button>
    </div>
  )
}
