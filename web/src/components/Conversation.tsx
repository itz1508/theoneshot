/**
 * Conversation — continuous chat view.
 * Scrolls independently. No permanent Input/Output/Thinking panels.
 */

import type { ChatMessage } from '../agent/types'
import { UserMessage } from './UserMessage'
import { AgentMessage } from './AgentMessage'
import { AgentLoadingState } from './AgentLoadingState'
import styles from './Conversation.module.css'

interface ConversationProps {
  messages: ChatMessage[]
  loading: boolean
}

export function Conversation({ messages, loading }: ConversationProps) {
  return (
    <div className={styles.conversation}>
      <div className={styles.list}>
        {messages.map((msg) =>
          msg.role === 'user' ? (
            <UserMessage key={msg.id} content={msg.content} />
          ) : (
            <AgentMessage key={msg.id} content={msg.content} activities={msg.activities} />
          ),
        )}
        {loading && <AgentLoadingState />}
      </div>
    </div>
  )
}
