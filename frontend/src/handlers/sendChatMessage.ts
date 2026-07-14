import { sendEvent, lengthBucket } from '../lib/telemetry'
import { FrontendEvent } from '../lib/telemetryEvents'
import type { Task, AgentMessage, ConversationItem } from '../types'

export interface SendChatApi {
  post(url: string, body: unknown): Promise<{ woken?: boolean; profile_switched?: boolean }>
}

export interface SendChatDeps {
  api: SendChatApi
  selectedTask: Task | null
  chatInput: string
  profileId: string
  conversationItems: ConversationItem[]
  sendingRef: React.MutableRefObject<boolean>
  setChatInput: (v: string) => void
  setAgentMessages: React.Dispatch<React.SetStateAction<AgentMessage[]>>
  setConversationItems: React.Dispatch<React.SetStateAction<ConversationItem[]>>
  setSelectedTask: React.Dispatch<React.SetStateAction<Task | null>>
  setTasks: React.Dispatch<React.SetStateAction<Task[]>>
}

/**
 * Send a follow-up message on the selected task. Optimistically appends
 * the user turn, POSTs it, and reconciles woken / profile-switch status
 * from the response. `sendingRef` de-dupes rapid double-submits.
 */
export async function sendChatMessage(deps: SendChatDeps): Promise<void> {
  const { selectedTask } = deps
  if (!selectedTask || !deps.chatInput.trim() || deps.sendingRef.current) return
  deps.sendingRef.current = true
  const content = deps.chatInput.trim()
  deps.setChatInput('')
  sendEvent(FrontendEvent.TASK_MESSAGE_SUBMITTED, {
    length_bucket: lengthBucket(content.length),
    task_status_at_send: selectedTask.status,
    is_first_message_for_task: !deps.conversationItems.some(c => c.type === 'user_message'),
  })
  // Optimistic add to both agentMessages (debug) and conversationItems
  deps.setAgentMessages(prev => [...prev, { role: 'user', content }])
  deps.setConversationItems(prev => [...prev, {
    id: `user-opt-${Date.now()}`,
    type: 'user_message',
    timestamp: new Date().toISOString(),
    message: { role: 'user', content },
  }])
  try {
    const response = await deps.api.post(`/api/tasks/${selectedTask.id}/messages`, { content, profile_id: deps.profileId })
    if (response.woken) {
      deps.setSelectedTask(prev => prev ? { ...prev, status: 'queued' } : prev)
      deps.setTasks(prev => prev.map(t2 => t2.id === selectedTask.id ? { ...t2, status: 'queued' } : t2))
    }
    if (response.profile_switched) {
      deps.setSelectedTask(prev => prev ? { ...prev, ai_profile_id: deps.profileId } : prev)
    }
  } catch (err) { console.error('Failed to send message:', err) } finally { deps.sendingRef.current = false }
}
