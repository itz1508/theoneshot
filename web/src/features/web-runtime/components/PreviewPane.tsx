/**
 * PreviewPane — renders the container's dev server in an iframe once the
 * runtime is ready; a placeholder before that. The iframe element is kept
 * stable so the in-container session survives feature switches (the whole
 * feature stays mounted, CSS-hidden, in App.tsx).
 */

import { Globe } from 'lucide-react'
import { useWebRuntimeStore } from '../store'
import styles from '../WebRuntime.module.css'

const STATUS_HINT: Record<string, string> = {
  booting: 'Booting WebContainer…',
  installing: 'Installing dependencies…',
  starting: 'Starting dev server…',
}

export function PreviewPane() {
  const previewUrl = useWebRuntimeStore((s) => s.previewUrl)
  const status = useWebRuntimeStore((s) => s.status)

  return (
    <div className={styles.previewArea}>
      {previewUrl ? (
        <iframe
          className={styles.previewFrame}
          src={previewUrl}
          title="Runtime preview"
          allow="cross-origin-isolated"
        />
      ) : (
        <div className={styles.previewPlaceholder}>
          <Globe size={24} aria-hidden="true" />
          <span>{STATUS_HINT[status] ?? 'Preview appears here once the dev server is ready.'}</span>
        </div>
      )}
    </div>
  )
}
