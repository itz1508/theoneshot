/**
 * Conversation — continuous chat view.
 * Scrolls independently through MessageScroller: the anchor mode (selected in
 * the composer toolbar) decides which row anchors each turn, prepended history
 * preserves the reader's position, and consumers can track the reader via
 * useMessageScrollerVisibility.
 */

import type { ChatMessage } from '../agent/types'
import { UserMessage } from './UserMessage'
import { AgentMessage } from './AgentMessage'
import { AgentLoadingState } from './AgentLoadingState'
import {
  MessageScroller,
  MessageScrollerViewport,
  MessageScrollerItem,
} from './ui/MessageScroller'
import type { AnchorMode } from './MessageComposer'
import styles from './Conversation.module.css'

interface ConversationProps {
  messages: ChatMessage[]
  loading: boolean
  anchorMode: AnchorMode
}

export function Conversation({ messages, loading, anchorMode }: ConversationProps) {
  const anchorRole: ChatMessage['role'] = anchorMode === 'assistant' ? 'agent' : 'user'

  return (
    <MessageScroller>
      <MessageScrollerViewport className={styles.conversation}>
        <div className={styles.list}>
          {messages.map((msg) => (
            <MessageScrollerItem
              key={msg.id}
              messageId={msg.id}
              scrollAnchor={msg.role === anchorRole}
            >
              {msg.role === 'user' ? (
                <UserMessage content={msg.content} />
              ) : (
                <AgentMessage content={msg.content} activities={msg.activities} />
              )}
            </MessageScrollerItem>
          ))}
          {loading && <AgentLoadingState />}
        </div>
      </MessageScrollerViewport>
    </MessageScroller>
  )
}
