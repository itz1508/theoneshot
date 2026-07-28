/**
 * MessageScroller — conversation viewport primitives.
 *
 * Anchors are explicit: any row may start a meaningful turn by setting
 * `scrollAnchor`. The scroller keeps a small previous-turn peek above a new
 * anchor and preserves the visible row when older history is prepended.
 */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useSyncExternalStore,
  type HTMLAttributes,
  type ReactNode,
  type RefObject,
} from 'react'
import styles from './MessageScroller.module.css'

interface ItemRecord {
  el: HTMLElement
  anchor: boolean
}

export interface MessageScrollerVisibility {
  currentAnchorId: string | null
  visibleMessageIds: string[]
}

interface MessageScrollerContextValue {
  viewportRef: RefObject<HTMLDivElement | null>
  anchorPeekRef: RefObject<number>
  registerItem: (id: string, el: HTMLElement, anchor: boolean) => () => void
  orderedItems: () => Array<[string, ItemRecord]>
  requestAnchorScroll: (el: HTMLElement) => void
  isInitialRender: () => boolean
  recomputeIfTracking: () => void
  subscribeVisibility: (cb: () => void) => () => void
  getVisibilitySnapshot: () => MessageScrollerVisibility
}

const MessageScrollerContext = createContext<MessageScrollerContextValue | null>(null)
const EMPTY_SNAPSHOT: MessageScrollerVisibility = { currentAnchorId: null, visibleMessageIds: [] }
const DEFAULT_ANCHOR_PEEK = 24

function useMessageScrollerContext(caller: string): MessageScrollerContextValue {
  const ctx = useContext(MessageScrollerContext)
  if (!ctx) throw new Error(`${caller} must be used within <MessageScroller>`)
  return ctx
}

function arraysEqual(a: string[], b: string[]): boolean {
  return a.length === b.length && a.every((value, index) => value === b[index])
}

function rowTop(viewport: HTMLElement, el: HTMLElement): number {
  return el.getBoundingClientRect().top - viewport.getBoundingClientRect().top + viewport.scrollTop
}

interface MessageScrollerProps {
  children: ReactNode
  className?: string
}

export function MessageScroller({ children, className = '' }: MessageScrollerProps) {
  const viewportRef = useRef<HTMLDivElement | null>(null)
  const anchorPeekRef = useRef(DEFAULT_ANCHOR_PEEK)
  const itemsRef = useRef(new Map<string, ItemRecord>())
  const initialRef = useRef(true)
  const listenersRef = useRef(new Set<() => void>())
  const snapshotRef = useRef<MessageScrollerVisibility>(EMPTY_SNAPSHOT)

  useEffect(() => {
    initialRef.current = false
  }, [])

  const orderedItems = useCallback(() => {
    const entries = Array.from(itemsRef.current.entries()).filter(([, record]) => record.el.isConnected)
    entries.sort(([, first], [, second]) =>
      first.el.compareDocumentPosition(second.el) & Node.DOCUMENT_POSITION_FOLLOWING ? -1 : 1,
    )
    return entries
  }, [])

  const recompute = useCallback(() => {
    const viewport = viewportRef.current
    if (!viewport) return
    const rect = viewport.getBoundingClientRect()
    const anchorLine = rect.top + rect.height * 0.5
    const visible: string[] = []
    let currentAnchorId: string | null = null
    for (const [id, record] of orderedItems()) {
      const row = record.el.getBoundingClientRect()
      if (row.bottom > rect.top && row.top < rect.bottom) visible.push(id)
      if (record.anchor && row.top <= anchorLine) currentAnchorId = id
    }
    const previous = snapshotRef.current
    if (previous.currentAnchorId === currentAnchorId && arraysEqual(previous.visibleMessageIds, visible)) return
    snapshotRef.current = { currentAnchorId, visibleMessageIds: visible }
    listenersRef.current.forEach((listener) => listener())
  }, [orderedItems])

  const recomputeIfTracking = useCallback(() => {
    if (listenersRef.current.size > 0) recompute()
  }, [recompute])

  const registerItem = useCallback((id: string, el: HTMLElement, anchor: boolean) => {
    itemsRef.current.set(id, { el, anchor })
    recomputeIfTracking()
    return () => {
      if (itemsRef.current.get(id)?.el === el) itemsRef.current.delete(id)
      recomputeIfTracking()
    }
  }, [recomputeIfTracking])

  const requestAnchorScroll = useCallback((el: HTMLElement) => {
    const viewport = viewportRef.current
    if (!viewport) return
    const target = Math.max(0, rowTop(viewport, el) - anchorPeekRef.current)
    if (typeof viewport.scrollTo === 'function') viewport.scrollTo({ top: target, behavior: 'smooth' })
    else viewport.scrollTop = target
  }, [])

  const subscribeVisibility = useCallback((listener: () => void) => {
    listenersRef.current.add(listener)
    recompute()
    return () => listenersRef.current.delete(listener)
  }, [recompute])

  const contextValue = useMemo<MessageScrollerContextValue>(() => ({
    viewportRef,
    anchorPeekRef,
    registerItem,
    orderedItems,
    requestAnchorScroll,
    isInitialRender: () => initialRef.current,
    recomputeIfTracking,
    subscribeVisibility,
    getVisibilitySnapshot: () => snapshotRef.current,
  }), [orderedItems, recomputeIfTracking, registerItem, requestAnchorScroll, subscribeVisibility])

  return <MessageScrollerContext.Provider value={contextValue}><div className={`${styles.root} ${className}`.trim()}>{children}</div></MessageScrollerContext.Provider>
}

interface MessageScrollerViewportProps extends HTMLAttributes<HTMLDivElement> {
  preserveScrollOnPrepend?: boolean
  anchorPeek?: number
  children: ReactNode
}

export function MessageScrollerViewport({
  preserveScrollOnPrepend = true,
  anchorPeek = DEFAULT_ANCHOR_PEEK,
  className = '',
  children,
  ...props
}: MessageScrollerViewportProps) {
  const ctx = useMessageScrollerContext('MessageScrollerViewport')

  useEffect(() => {
    ctx.anchorPeekRef.current = anchorPeek
  }, [anchorPeek, ctx])

  useEffect(() => {
    const viewport = ctx.viewportRef.current
    if (!viewport) return
    const onScrollOrResize = () => ctx.recomputeIfTracking()
    viewport.addEventListener('scroll', onScrollOrResize, { passive: true })
    window.addEventListener('resize', onScrollOrResize)
    return () => {
      viewport.removeEventListener('scroll', onScrollOrResize)
      window.removeEventListener('resize', onScrollOrResize)
    }
  }, [ctx])

  useEffect(() => {
    const viewport = ctx.viewportRef.current
    if (!viewport || !preserveScrollOnPrepend) return
    const reference = { el: null as HTMLElement | null, top: 0 }
    const markReference = () => {
      reference.el = null
      for (const [, record] of ctx.orderedItems()) {
        const top = rowTop(viewport, record.el)
        if (top + record.el.getBoundingClientRect().height > viewport.scrollTop) {
          reference.el = record.el
          reference.top = top
          break
        }
      }
    }
    markReference()
    viewport.addEventListener('scroll', markReference, { passive: true })
    const observer = new MutationObserver(() => {
      if (reference.el && reference.el.isConnected) {
        const delta = rowTop(viewport, reference.el) - reference.top
        if (delta > 0) viewport.scrollTop += delta
      }
      markReference()
    })
    observer.observe(viewport, { childList: true, subtree: true })
    return () => {
      observer.disconnect()
      viewport.removeEventListener('scroll', markReference)
    }
  }, [ctx, preserveScrollOnPrepend])

  return <div ref={ctx.viewportRef} className={`${styles.viewport} ${className}`.trim()} {...props}>{children}</div>
}

interface MessageScrollerItemProps extends HTMLAttributes<HTMLDivElement> {
  messageId?: string
  scrollAnchor?: boolean
  children: ReactNode
}

export function MessageScrollerItem({ messageId, scrollAnchor = false, className = '', children, ...props }: MessageScrollerItemProps) {
  const ctx = useMessageScrollerContext('MessageScrollerItem')
  const elRef = useRef<HTMLDivElement | null>(null)

  useLayoutEffect(() => {
    if (!elRef.current || !messageId) return
    return ctx.registerItem(messageId, elRef.current, scrollAnchor)
  }, [ctx, messageId, scrollAnchor])

  useLayoutEffect(() => {
    if (!elRef.current || !scrollAnchor || ctx.isInitialRender()) return
    ctx.requestAnchorScroll(elRef.current)
    // Anchoring is an append-time behavior. Changing the selected anchor role
    // must not move existing rows; the next appended matching row will anchor.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return <div ref={elRef} data-message-id={messageId} className={`${styles.item} ${className}`.trim()} {...props}>{children}</div>
}

export function useMessageScrollerVisibility(): MessageScrollerVisibility {
  const ctx = useMessageScrollerContext('useMessageScrollerVisibility')
  return useSyncExternalStore(ctx.subscribeVisibility, ctx.getVisibilitySnapshot)
}
