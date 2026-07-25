/**
 * WorkspaceRoot — a single project root in the Explorer.
 * Shows expand/collapse, file tree, Sandbox badge, and ActivityLED.
 */

import { useState, useCallback } from 'react'
import { FolderOpen, File, ChevronRight } from 'lucide-react'
import type { Workspace, FileNode } from '../agent/types'
import { ActivityLED } from './ActivityLED'
import styles from './WorkspaceRoot.module.css'

interface WorkspaceRootProps {
  workspace: Workspace
  onLEDClick: () => void
}

function FileTreeNode({ node, depth }: { node: FileNode; depth: number }) {
  const [expanded, setExpanded] = useState(depth === 0)

  const toggle = useCallback(() => {
    if (node.type === 'folder') setExpanded((prev) => !prev)
  }, [node.type])

  if (node.type === 'folder') {
    return (
      <div>
        <button
          className={styles.row}
          style={{ paddingLeft: 8 + depth * 14 }}
          onClick={toggle}
        >
          <ChevronRight
            size={10}
            className={`${styles.chevron} ${expanded ? styles.chevronOpen : ''}`}
          />
          <FolderOpen size={12} className={styles.folderIcon} />
          <span className={styles.folderName}>{node.name}</span>
        </button>
        {expanded && node.children && (
          <div>
            {node.children.map((child) => (
              <FileTreeNode key={child.id} node={child} depth={depth + 1} />
            ))}
          </div>
        )}
      </div>
    )
  }

  return (
    <button
      className={styles.row}
      style={{ paddingLeft: 8 + depth * 14 }}
    >
      <span className={styles.fileIndent} />
      <File size={11} className={styles.fileIcon} />
      <span className={styles.fileName}>{node.name}</span>
    </button>
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
