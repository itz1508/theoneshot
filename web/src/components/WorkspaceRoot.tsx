/**
 * WorkspaceRoot — a single project root in the Explorer.
 * Uses nested <details>/<summary> for a native collapsible file tree.
 */

import { useState } from 'react'
import { FolderOpen, File, ChevronRight } from 'lucide-react'
import type { Workspace, FileNode } from '../agent/types'
import { ActivityLED } from './ActivityLED'
import styles from './WorkspaceRoot.module.css'

interface WorkspaceRootProps {
  workspace: Workspace
  onLEDClick: () => void
}

function FileTreeNode({ node, depth }: { node: FileNode; depth: number }) {
  if (node.type === 'folder') {
    return (
      <details open={depth === 0} className={styles.details}>
        <summary className={styles.row} style={{ paddingLeft: 8 + depth * 14 }}>
          <ChevronRight size={10} className={styles.chevron} />
          <FolderOpen size={12} className={styles.folderIcon} />
          <span className={styles.folderName}>{node.name}</span>
        </summary>
        {node.children && (
          <div>
            {node.children.map((child) => (
              <FileTreeNode key={child.id} node={child} depth={depth + 1} />
            ))}
          </div>
        )}
      </details>
    )
  }

  return (
    <div className={styles.row} style={{ paddingLeft: 8 + depth * 14 }}>
      <span className={styles.fileIndent} />
      <File size={11} className={styles.fileIcon} />
      <span className={styles.fileName}>{node.name}</span>
    </div>
  )
}

export function WorkspaceRoot({ workspace, onLEDClick }: WorkspaceRootProps) {
  const [expanded, setExpanded] = useState(true)

  return (
    <div className={styles.root}>
      <div className={styles.header}>
        <button className={styles.expandBtn} onClick={() => setExpanded((prev) => !prev)}>
          <ChevronRight
            size={12}
            className={`${styles.rootChevron} ${expanded ? styles.rootChevronOpen : ''}`}
          />
        </button>
        <span className={styles.name}>{workspace.name}</span>
        <span className={styles.badge}>Sandbox</span>
        <span className={styles.spacer} />
        <ActivityLED stage={workspace.stage} onClick={onLEDClick} />
      </div>
      {expanded && (
        <div className={styles.tree}>
          {workspace.files.map((node) => (
            <FileTreeNode key={node.id} node={node} depth={0} />
          ))}
        </div>
      )}
    </div>
  )
}
