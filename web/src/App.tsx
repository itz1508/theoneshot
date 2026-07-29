/**
 * App — shell layout wiring all components to the central store.
 * The BackendChatSource is bound once at mount via the store.
 * No component imports the event source directly.
 */

import { lazy, Suspense, useEffect, useState } from 'react'
import { TopMenu } from './components/TopMenu'
import { ActivityRail, type RailTab } from './components/ActivityRail'
import { Explorer } from './components/Explorer'
import { Conversation } from './components/Conversation'
import { MessageComposer, type AnchorMode } from './components/MessageComposer'
import { TurnIndicator } from './components/TurnIndicator'
import { TaskReviewDrawer } from './components/TaskReviewDrawer'
import { WritingDesignAssistant } from './features/writing-design-assistant/WritingDesignAssistant'
import { useAppStore } from './store/taskStore'
import { BackendChatSource } from './agent/BackendChatSource'
import styles from './App.module.css'

// Lazy chunk on purpose: the Web Runtime feature (incl. its sample project)
// must stay out of the entry bundle until the tab is first opened.
const WebRuntime = lazy(() =>
  import('./features/web-runtime/WebRuntime').then((m) => ({ default: m.WebRuntime })),
)

// Instantiate event source once (module-level singleton)
const eventSource = new BackendChatSource()

function App() {
  const [railTab, setRailTab] = useState<RailTab>('explorer')
  const [explorerOpen, setExplorerOpen] = useState(true)
  const [anchorMode, setAnchorMode] = useState<AnchorMode>('user')
  // Latched on first open; the feature then stays mounted (CSS-hidden when
  // inactive) so the WebContainer session survives feature switches.
  const [webRuntimeOpened, setWebRuntimeOpened] = useState(false)

  const workspaces = useAppStore((s) => s.workspaces)
  const participatingWorkspaceIds = useAppStore((s) => s.participatingWorkspaceIds)
  const task = useAppStore((s) => s.task)
  const messages = useAppStore((s) => s.messages)
  const loading = useAppStore((s) => s.loading)
  const turn = useAppStore((s) => s.turn)
  const drawerOpen = useAppStore((s) => s.drawerOpen)
  const runnerMode = useAppStore((s) => s.runnerMode)
  const bindEventSource = useAppStore((s) => s.bindEventSource)
  const sendMessage = useAppStore((s) => s.sendMessage)
  const cancelTask = useAppStore((s) => s.cancelTask)
  const toggleDrawer = useAppStore((s) => s.toggleDrawer)
  const openDrawerForWorkspace = useAppStore((s) => s.openDrawerForWorkspace)

  // Bind the event source once
  useEffect(() => {
    bindEventSource(eventSource)
    return () => { eventSource.dispose() }
  }, [bindEventSource])

  // Activity rail toggles the explorer panel
  const handleRailSelect = (tab: RailTab) => {
    setRailTab(tab)
    if (tab === 'explorer') {
      setExplorerOpen((prev) => !prev)
    }
    if (tab === 'webruntime') {
      setWebRuntimeOpened(true)
    }
  }

  return (
    <div className={styles.shell}>
      <TopMenu runnerMode={runnerMode} loading={loading} />
      <div className={styles.body}>
        <ActivityRail active={railTab} onSelect={handleRailSelect} />
        <Explorer
          workspaces={workspaces}
          participatingWorkspaceIds={participatingWorkspaceIds}
          collapsed={!explorerOpen}
          onLEDClick={openDrawerForWorkspace}
        />
        <main className={styles.main}>
          {railTab === 'assistant' ? (
            <WritingDesignAssistant />
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
            <div
              className={`${styles.webRuntimeHost} ${railTab !== 'webruntime' ? styles.webRuntimeHidden : ''}`}
            >
              <Suspense fallback={null}>
                <WebRuntime />
              </Suspense>
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
