/**
 * Explorer — shadcn Card with two tab sections: [Explorer] [Outline].
 * File tree lives inside the Explorer tab; Outline tab shows a placeholder.
 */

import { Plus } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs'
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

  const fileTree = (
    <div className="flex flex-col gap-0.5">
      {grouped.map((ws) => (
        <WorkspaceRoot key={ws.id} workspace={ws} onLEDClick={() => onLEDClick(ws.id)} />
      ))}
      {ungrouped.map((ws) => (
        <WorkspaceRoot key={ws.id} workspace={ws} onLEDClick={() => onLEDClick(ws.id)} />
      ))}
    </div>
  )

  return (
    <div
      className="flex-shrink-0 overflow-hidden transition-[width] duration-200 ease-in-out border-r border-border"
      style={{ width: collapsed ? 0 : 260 }}
    >
      <div className="w-[260px] min-w-[260px] h-full flex flex-col bg-background">
        <Tabs defaultValue="explorer" className="flex flex-col flex-1 overflow-hidden">
          <div className="px-2 pt-2">
            <TabsList className="w-full">
              <TabsTrigger value="explorer">Explorer</TabsTrigger>
              <TabsTrigger value="outline">Outline</TabsTrigger>
            </TabsList>
          </div>

          <TabsContent value="explorer" className="flex-1 overflow-y-auto px-2 mt-1">
            <Card className="gap-2 border-border bg-card">
              <CardContent className="px-1 pb-2 pt-1">
                {fileTree}
              </CardContent>
            </Card>
          </TabsContent>

          <TabsContent value="outline" className="flex-1 overflow-y-auto px-2 mt-1">
            <Card className="gap-2 border-border bg-card">
              <CardContent className="px-3 py-6 text-center text-xs text-muted-foreground">
                No outline available
              </CardContent>
            </Card>
          </TabsContent>
        </Tabs>

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
