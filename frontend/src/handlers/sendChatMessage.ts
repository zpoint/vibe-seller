import { sendEvent, lengthBucket } from '../lib/telemetry'
import { FrontendEvent } from '../lib/telemetryEvents'
import type { Task, AgentMessage, ConversationItem, StagedAttachment } from '../types'

interface SendChatResponse {
  woken?: boolean
  profile_switched?: boolean
  // Canonical transcript content (prose + markdown-image attachments) the
  // server stored — the client echoes it so the optimistic bubble matches
  // the SSE copy exactly (dedup by content) and shows thumbnails.
  content?: string
}

export interface SendChatApi {
  post(url: string, body: unknown): Promise<SendChatResponse>
}

export interface SendChatDeps {
  api: SendChatApi
  selectedTask: Task | null
  chatInput: string
  attachments: StagedAttachment[]
  profileId: string
  conversationItems: ConversationItem[]
  sendingRef: React.MutableRefObject<boolean>
  setChatInput: (v: string) => void
  setAttachments: React.Dispatch<React.SetStateAction<StagedAttachment[]>>
  setAgentMessages: React.Dispatch<React.SetStateAction<AgentMessage[]>>
  setConversationItems: React.Dispatch<React.SetStateAction<ConversationItem[]>>
  setSelectedTask: React.Dispatch<React.SetStateAction<Task | null>>
  setTasks: React.Dispatch<React.SetStateAction<Task[]>>
}

/**
 * Send a follow-up message on the selected task. Sends the typed prose
 * plus any staged attachment ids; the server promotes the attachments and
 * returns the canonical transcript content, which we render (thumbnails,
 * not paths). Sends fine with text only, attachments only, or both.
 * `sendingRef` de-dupes rapid double-submits.
 */
export async function sendChatMessage(deps: SendChatDeps): Promise<void> {
  const { selectedTask } = deps
  const content = deps.chatInput.trim()
  const attachmentIds = deps.attachments.map(a => a.id)
  if (!selectedTask || (!content && !attachmentIds.length) || deps.sendingRef.current) return
  deps.sendingRef.current = true
  deps.setChatInput('')
  deps.setAttachments([])
  sendEvent(FrontendEvent.TASK_MESSAGE_SUBMITTED, {
    length_bucket: lengthBucket(content.length),
    task_status_at_send: selectedTask.status,
    is_first_message_for_task: !deps.conversationItems.some(c => c.type === 'user_message'),
  })
  try {
    const response = await deps.api.post(
      `/api/tasks/${selectedTask.id}/messages`,
      { content, profile_id: deps.profileId, attachment_ids: attachmentIds },
    )
    // Render the server's canonical content (falls back to prose if the
    // response predates this field). SSE will dedup its own copy by content.
    const canonical = response.content ?? content
    deps.setAgentMessages(prev => [...prev, { role: 'user', content: canonical }])
    deps.setConversationItems(prev => {
      const dup = prev.some(c =>
        c.type === 'user_message' && c.message?.content === canonical &&
        Date.now() - new Date(c.timestamp).getTime() < 5000)
      if (dup) return prev
      return [...prev, {
        id: `user-opt-${Date.now()}`,
        type: 'user_message',
        timestamp: new Date().toISOString(),
        message: { role: 'user', content: canonical },
      }]
    })
    if (response.woken) {
      deps.setSelectedTask(prev => prev ? { ...prev, status: 'queued' } : prev)
      deps.setTasks(prev => prev.map(t2 => t2.id === selectedTask.id ? { ...t2, status: 'queued' } : t2))
    }
    if (response.profile_switched) {
      deps.setSelectedTask(prev => prev ? { ...prev, ai_profile_id: deps.profileId } : prev)
    }
  } catch (err) { console.error('Failed to send message:', err) } finally { deps.sendingRef.current = false }
}
