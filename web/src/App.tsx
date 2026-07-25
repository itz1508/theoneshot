/**
 * App — shell layout wiring all components to the central store.
 * The DeterministicEventSource is bound once at mount via the store.
 * No component imports the mock emitter directly.
 */

import { useEffect, useState } from 'react'
import { TopMenu } from './components/TopMenu'
import { ActivityRail, type RailTab } from './components/ActivityRail'
import { Explorer } from './components/Explorer'
import { Conversation } from './components/Conversation'
import { MessageComposer } from './components/MessageComposer'
import { TaskReviewDrawer } from './components/TaskReviewDrawer'
import { useAppStore } from './store/taskStore'
import { DemoTaskEventSource } from './agent/DemoTaskEventSource'
import styles from './App.module.css'

// Instantiate event source once (module-level singleton)
const eventSource = new DemoTaskEventSource()

function App() {
  const [railTab, setRailTab] = useState<RailTab>('explorer')
  const [explorerOpen, setExplorerOpen] = useState(true)

  const workspaces = useAppStore((s) => s.workspaces)
  const participatingWorkspaceIds = useAppStore((s) => s.participatingWorkspaceIds)
  const task = useAppStore((s) => s.task)
  const messages = useAppStore((s) => s.messages)
  const loading = useAppStore((s) => s.loading)
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
          <Conversation messages={messages} loading={loading} />
          <MessageComposer onSend={sendMessage} />
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
