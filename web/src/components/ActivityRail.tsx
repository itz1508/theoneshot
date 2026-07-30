/**
 * ActivityRail — 52px vertical icon rail on the left.
 * Remains visible when the contextual panel collapses.
 */

import { FolderOpen, Bug, Video, PenLine, AppWindow, Workflow } from 'lucide-react'
import styles from './ActivityRail.module.css'

export type RailTab = 'explorer' | 'debug' | 'video' | 'assistant' | 'webruntime' | 'aflow'

interface ActivityRailProps {
  active: RailTab
  onSelect: (tab: RailTab) => void
  aflowUnread?: number
}

const tabs: { id: RailTab; icon: typeof FolderOpen; label: string }[] = [
  { id: 'explorer', icon: FolderOpen, label: 'Explorer' },
  { id: 'debug', icon: Bug, label: 'Debug' },
  { id: 'video', icon: Video, label: 'Video' },
  { id: 'assistant', icon: PenLine, label: 'Writing & Design Assistant' },
  { id: 'webruntime', icon: AppWindow, label: 'Web Runtime' },
  { id: 'aflow', icon: Workflow, label: 'A-Flow Management' },
]

export function ActivityRail({ active, onSelect, aflowUnread = 0 }: ActivityRailProps) {
  return (
    <nav className={styles.rail} aria-label="Activity rail">
      {tabs.map((tab) => {
        const Icon = tab.icon
        return (
          <button
            key={tab.id}
            className={`${styles.btn} ${active === tab.id ? styles.active : ''}`}
            onClick={() => onSelect(tab.id)}
            title={tab.label}
            aria-label={tab.label}
            aria-pressed={active === tab.id}
          >
            <Icon size={20} strokeWidth={1.6} />
            {tab.id === 'aflow' && aflowUnread > 0 ? (
              <span className={styles.badge} aria-label={`${aflowUnread} unread A-Flow issues`}>
                {aflowUnread > 9 ? '9+' : aflowUnread}
              </span>
            ) : null}
          </button>
        )
      })}
    </nav>
  )
}
