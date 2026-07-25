import { createContext, useContext, type ReactNode, type ButtonHTMLAttributes, type AnchorHTMLAttributes } from 'react'
import styles from './Attachment.module.css'

/* ─── Types ─── */
type AttachmentState = 'idle' | 'uploading' | 'processing' | 'error' | 'done'
type AttachmentSize = 'sm' | 'md' | 'lg'
type AttachmentOrientation = 'horizontal' | 'vertical'

interface AttachmentContextValue {
  state: AttachmentState
  size: AttachmentSize
  orientation: AttachmentOrientation
}

const AttachmentContext = createContext<AttachmentContextValue>({
  state: 'idle',
  size: 'md',
  orientation: 'horizontal',
})

function useAttachment() {
  return useContext(AttachmentContext)
}

/* ─── AttachmentGroup ─── */
interface AttachmentGroupProps {
  children: ReactNode
  className?: string
}

export function AttachmentGroup({ children, className = '' }: AttachmentGroupProps) {
  return (
    <div className={`${styles.group} ${className}`}>
      <div className={styles.groupScroll}>
        {children}
      </div>
    </div>
  )
}

/* ─── Attachment (Root) ─── */
interface AttachmentProps {
  state?: AttachmentState
  size?: AttachmentSize
  orientation?: AttachmentOrientation
  className?: string
  children: ReactNode
}

export function Attachment({
  state = 'idle',
  size = 'md',
  orientation = 'horizontal',
  className = '',
  children,
}: AttachmentProps) {
  const inProgress = state === 'uploading' || state === 'processing'
  return (
    <AttachmentContext.Provider value={{ state, size, orientation }}>
      <div
        className={[
          styles.root,
          styles[`state_${state}`],
          styles[`size_${size}`],
          styles[`orient_${orientation}`],
          inProgress ? styles.shimmer : '',
          className,
        ].filter(Boolean).join(' ')}
      >
        {children}
      </div>
    </AttachmentContext.Provider>
  )
}

/* ─── AttachmentMedia ─── */
interface AttachmentMediaProps {
  children: ReactNode
  className?: string
}

export function AttachmentMedia({ children, className = '' }: AttachmentMediaProps) {
  const { state } = useAttachment()
  return (
    <div className={`${styles.media} ${styles[`media_${state}`]} ${className}`}>
      {children}
    </div>
  )
}

/* ─── AttachmentContent ─── */
interface AttachmentContentProps {
  children: ReactNode
  className?: string
}

export function AttachmentContent({ children, className = '' }: AttachmentContentProps) {
  return <div className={`${styles.content} ${className}`}>{children}</div>
}

/* ─── AttachmentTitle ─── */
interface AttachmentTitleProps {
  children: ReactNode
  className?: string
}

export function AttachmentTitle({ children, className = '' }: AttachmentTitleProps) {
  return <span className={`${styles.title} ${className}`}>{children}</span>
}

/* ─── AttachmentDescription ─── */
interface AttachmentDescriptionProps {
  children: ReactNode
  className?: string
}

export function AttachmentDescription({ children, className = '' }: AttachmentDescriptionProps) {
  return <span className={`${styles.description} ${className}`}>{children}</span>
}

/* ─── AttachmentActions ─── */
interface AttachmentActionsProps {
  children: ReactNode
  className?: string
}

export function AttachmentActions({ children, className = '' }: AttachmentActionsProps) {
  return <div className={`${styles.actions} ${className}`}>{children}</div>
}

/* ─── AttachmentAction ─── */
type AttachmentActionProps = ButtonHTMLAttributes<HTMLButtonElement> & { className?: string }

export function AttachmentAction({ children, className = '', ...props }: AttachmentActionProps) {
  return (
    <button className={`${styles.action} ${className}`} {...props}>
      {children}
    </button>
  )
}

/* ─── AttachmentTrigger ─── */
type AttachmentTriggerProps = {
  children?: ReactNode
  className?: string
  href?: string
  onClick?: () => void
} & Omit<AnchorHTMLAttributes<HTMLAnchorElement>, 'className'>

export function AttachmentTrigger({ children, className = '', href, onClick, ...props }: AttachmentTriggerProps) {
  if (href) {
    return (
      <a className={`${styles.trigger} ${className}`} href={href} {...props}>
        {children}
      </a>
    )
  }
  return (
    <button className={`${styles.trigger} ${className}`} onClick={onClick} type="button">
      {children}
    </button>
  )
}
