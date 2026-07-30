/** App shell wiring Operator Chat, feature tabs, and A-Flow notifications. */

import { lazy, Suspense, useEffect, useState } from 'react'
import { TopMenu } from './components/TopMenu'
import { ActivityRail, type RailTab } from './components/ActivityRail'
import { Explorer } from './components/Explorer'
import { Conversation } from './components/Conversation'
import { MessageComposer, type AnchorMode } from './components/MessageComposer'
import { TurnIndicator } from './components/TurnIndicator'
import { TaskReviewDrawer } from './components/TaskReviewDrawer'
import { WritingDesignAssistant } from './features/writing-design-assistant/WritingDesignAssistant'
import { useAflowManagement } from './features/aflow-management/useAflowManagement'
import { useAppStore } from './store/taskStore'
import { BackendChatSource } from './agent/BackendChatSource'
import styles from './App.module.css'

const WebRuntime = lazy(() =>
  import('./features/web-runtime/WebRuntime').then((module) => ({ default: module.WebRuntime })),
)
const AflowManagement = lazy(() => import('./features/aflow-management/AflowManagement'))

const eventSource = new BackendChatSource()

function App() {
  const [railTab, setRailTab] = useState<RailTab>('explorer')
  const [explorerOpen, setExplorerOpen] = useState(true)
  const [anchorMode, setAnchorMode] = useState<AnchorMode>('user')
  const [webRuntimeOpened, setWebRuntimeOpened] = useState(false)
  const aflow = useAflowManagement()

  const workspaces = useAppStore((state) => state.workspaces)
  const participatingWorkspaceIds = useAppStore((state) => state.participatingWorkspaceIds)
  const task = useAppStore((state) => state.task)
  const messages = useAppStore((state) => state.messages)
  const loading = useAppStore((state) => state.loading)
  const turn = useAppStore((state) => state.turn)
  const drawerOpen = useAppStore((state) => state.drawerOpen)
  const runnerMode = useAppStore((state) => state.runnerMode)
  const bindEventSource = useAppStore((state) => state.bindEventSource)
  const sendMessage = useAppStore((state) => state.sendMessage)
  const cancelTask = useAppStore((state) => state.cancelTask)
  const toggleDrawer = useAppStore((state) => state.toggleDrawer)
  const openDrawerForWorkspace = useAppStore((state) => state.openDrawerForWorkspace)

  useEffect(() => {
    bindEventSource(eventSource)
    return () => eventSource.dispose()
  }, [bindEventSource])

  const handleRailSelect = (tab: RailTab) => {
    setRailTab(tab)
    if (tab === 'explorer') setExplorerOpen((open) => !open)
    if (tab === 'webruntime') setWebRuntimeOpened(true)
    if (tab === 'aflow') aflow.markSeen()
  }

  return (
    <div className={styles.shell}>
      <TopMenu runnerMode={runnerMode} loading={loading} />
      <div className={styles.body}>
        <ActivityRail active={railTab} onSelect={handleRailSelect} aflowUnread={aflow.unreadCount} />
        <Explorer
          workspaces={workspaces}
          participatingWorkspaceIds={participatingWorkspaceIds}
          collapsed={!explorerOpen}
          onLEDClick={openDrawerForWorkspace}
        />
        <main className={styles.main}>
          {aflow.latest && aflow.unreadCount > 0 && railTab !== 'aflow' ? (
            <div className={styles.aflowBanner} role="alert">
              <span>
                A-Flow issue: {aflow.latest.issue_code.split('_').join(' ')} during {aflow.latest.stage}.
              </span>
              <button onClick={() => handleRailSelect('aflow')}>Open A-Flow</button>
            </div>
          ) : null}
          {railTab === 'assistant' ? (
            <WritingDesignAssistant />
          ) : railTab === 'aflow' ? (
            <Suspense fallback={null}>
              <AflowManagement
                status={aflow.status}
                issues={aflow.issues}
                error={aflow.error}
                onRefresh={aflow.refresh}
              />
            </Suspense>
          ) : railTab !== 'webruntime' ? (
            <>
              <Conversation messages={messages} loading={loading} anchorMode={anchorMode} />
              <TurnIndicator turn={turn} />
              <MessageComposer
                onSend={sendMessage}
                anchorMode={anchorMode}
                onAnchorModeChange={setAnchorMode}
                disabled={turn !== 'user'}
              />
            </>
          ) : null}
          {webRuntimeOpened ? (
            <div className={`${styles.webRuntimeHost} ${railTab !== 'webruntime' ? styles.webRuntimeHidden : ''}`}>
              <Suspense fallback={null}><WebRuntime /></Suspense>
            </div>
          ) : null}
        </main>
        <TaskReviewDrawer
          open={drawerOpen}
          task={task}
          runnerMode={runnerMode}
          onToggle={toggleDrawer}
          onCancel={cancelTask}
        />
      </div>
    </div>
  )
}

export default App
