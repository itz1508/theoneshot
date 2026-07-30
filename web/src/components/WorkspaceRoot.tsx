/**
 * WorkspaceRoot — a single project root in the Explorer.
 * Uses nested shadcn Collapsibles to build a file tree.
 */

import { useState } from 'react'
import { ChevronRightIcon, FileIcon, FolderIcon } from 'lucide-react'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import { cn } from '@/lib/utils'
import type { Workspace, FileNode } from '../agent/types'
import { ActivityLED } from './ActivityLED'
import styles from './WorkspaceRoot.module.css'

interface WorkspaceRootProps {
  workspace: Workspace
  onLEDClick: () => void
}

/* ── Recursive file-tree node ── */

function FileTreeNode({ node, depth }: { node: FileNode; depth: number }) {
  if (node.type === 'folder') {
    return (
      <Collapsible defaultOpen={depth === 0} className={styles.folderNode}>
        <CollapsibleTrigger
          className={styles.row}
          style={{ paddingLeft: 8 + depth * 16 }}
        >
          <ChevronRightIcon size={10} className={styles.chevron} />
          <FolderIcon size={12} className={styles.folderIcon} />
          <span className={styles.folderName}>{node.name}</span>
        </CollapsibleTrigger>
        <CollapsibleContent className={styles.collapsibleContent}>
          {node.children?.map((child) => (
            <FileTreeNode key={child.id} node={child} depth={depth + 1} />
          ))}
        </CollapsibleContent>
      </Collapsible>
    )
  }

  return (
    <div className={styles.fileRow} style={{ paddingLeft: 8 + depth * 16 }}>
      <FileIcon size={11} className={styles.fileIcon} />
      <span className={styles.fileName}>{node.name}</span>
    </div>
  )
}

/* ── Workspace root with header + tree ── */

export function WorkspaceRoot({ workspace, onLEDClick }: WorkspaceRootProps) {
  const [expanded, setExpanded] = useState(true)

  return (
    <div className={styles.root}>
      <div className={styles.header}>
        <button className={styles.expandBtn} onClick={() => setExpanded((prev) => !prev)}>
          <ChevronRightIcon
            size={12}
            className={cn(styles.rootChevron, expanded && styles.rootChevronOpen)}
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
