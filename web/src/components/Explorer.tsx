/**
 * Explorer — shadcn Card with Tabs header wrapping a collapsible file tree.
 * Reference: shadcn collapsible file tree pattern.
 */

import { Plus } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader } from '@/components/ui/card'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { WorkspaceRoot } from './WorkspaceRoot'
import type { Workspace } from '../agent/types'

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
    <div
      className="flex-shrink-0 overflow-hidden transition-[width] duration-200 ease-in-out border-r border-border"
      style={{ width: collapsed ? 0 : 260 }}
    >
      <div className="w-[260px] min-w-[260px] h-full flex flex-col bg-background">
        <Card className="mx-2 mt-2 mb-1 gap-2">
          <CardHeader className="px-2 pt-2 pb-0">
            <Tabs defaultValue="explorer">
              <TabsList className="w-full">
                <TabsTrigger value="explorer">Explorer</TabsTrigger>
                <TabsTrigger value="outline">Outline</TabsTrigger>
              </TabsList>
            </Tabs>
          </CardHeader>
          <CardContent className="px-1 pb-2">
            <div className="flex flex-col gap-0.5">
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
            </div>
          </CardContent>
        </Card>

        {/* Spacer pushes add-btn to bottom */}
        <div className="flex-1" />

        <Button
          variant="ghost"
          size="sm"
          className="w-full justify-start gap-1.5 text-muted-foreground border-t border-border rounded-none h-9"
        >
          <Plus className="size-3.5" />
          <span className="text-xs">Add folder</span>
        </Button>
      </div>
    </div>
  )
}
