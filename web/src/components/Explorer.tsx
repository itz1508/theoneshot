/**
 * Explorer — collapsible panel showing multiple independent workspace roots.
 * File tree is wrapped in a shadcn Card for a built-in container layer.
 */

import { Plus } from 'lucide-react'
import { Card, CardContent, CardHeader } from '@/components/ui/card'
import { WorkspaceRoot } from './WorkspaceRoot'
import type { Workspace } from '../agent/types'
import styles from './Explorer.module.css'

interface ExplorerProps {
  workspaces: Workspace[]
  participatingWorkspaceIds: string[]
  collapsed: boolean
  onLEDClick: (workspaceId: string) => void
}

export function Explorer({ workspaces, participatingWorkspaceIds, collapsed, onLEDClick }: ExplorerProps) {
  const grouped = workspaces.filter((ws) => participatingWorkspaceIds.includes(ws.id))
  const ungrouped = workspaces.filter((ws) => !participatingWorkspaceIds.includes(ws.id))

  return (
    <div className={styles.panel} style={{ width: collapsed ? 0 : 260 }}>
      <div className={styles.inner}>
        <div className={styles.header}>
          <span className={styles.title}>Explorer</span>
        </div>

        <Card className={styles.treeCard}>
          <CardHeader className={styles.treeCardHeader}>
            <span className={styles.treeCardTitle}>Files</span>
          </CardHeader>
          <CardContent className={styles.treeCardContent}>
            {/* Task-grouped workspaces */}
            {grouped.map((ws) => (
              <WorkspaceRoot
                key={ws.id}
                workspace={ws}
                onLEDClick={() => onLEDClick(ws.id)}
              />
            ))}

            {/* Independent workspaces */}
            {ungrouped.map((ws) => (
              <WorkspaceRoot
                key={ws.id}
                workspace={ws}
                onLEDClick={() => onLEDClick(ws.id)}
              />
            ))}
          </CardContent>
        </Card>

        <button className={styles.addBtn}>
          <Plus size={14} />
          <span>Add folder</span>
        </button>
      </div>
    </div>
  )
}
