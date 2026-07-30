import { useCallback, useEffect, useMemo, useState } from 'react'
import { getAflowIssues, getAflowStatus } from './api'
import type { AflowIssuePage, AflowStatus } from './types'

const POLL_MS = 10_000

export function useAflowManagement() {
  const [status, setStatus] = useState<AflowStatus | null>(null)
  const [issues, setIssues] = useState<AflowIssuePage | null>(null)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    try {
      const [nextStatus, nextIssues] = await Promise.all([
        getAflowStatus(),
        getAflowIssues(),
      ])
      setStatus(nextStatus)
      setIssues(nextIssues)
      setError(null)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    }
  }, [])

  useEffect(() => {
    void refresh()
    const interval = window.setInterval(refresh, POLL_MS)
    const immediate = () => void refresh()
    window.addEventListener('aflow_issue_created', immediate)
    return () => {
      window.clearInterval(interval)
      window.removeEventListener('aflow_issue_created', immediate)
    }
  }, [refresh])

  const latest = issues?.items[0] ?? null
  const lastSeenKey = status ? `aflow-last-seen:${status.workspace_id}` : null
  const lastSeen = lastSeenKey ? window.localStorage.getItem(lastSeenKey) : null
  const unreadCount = useMemo(() => {
    if (!issues || !lastSeen) return issues?.items.length ?? 0
    const index = issues.items.findIndex((issue) => issue.issue_id === lastSeen)
    return index < 0 ? issues.items.length : index
  }, [issues, lastSeen])

  const markSeen = useCallback(() => {
    if (lastSeenKey && latest) window.localStorage.setItem(lastSeenKey, latest.issue_id)
  }, [lastSeenKey, latest])

  return { status, issues, error, unreadCount, latest, refresh, markSeen }
}
