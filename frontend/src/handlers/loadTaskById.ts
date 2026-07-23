import { buildConversationItems } from '../lib/conversation'
import type { Task, TaskStep, TodoItem, AgentMessage, ConversationItem, StagedAttachment } from '../types'

interface PendingQuestions {
  request_id: string
  questions: { header?: string; question: string; options?: { label: string; description?: string }[] }[]
}

export interface LoadTaskDeps {
  api: { get(url: string): Promise<unknown> }
  // Monotonic token ref shared with App: each call claims the next value;
  // a load only applies its state while it is still the current one.
  seqRef: React.MutableRefObject<number>
  setSelectedTask: React.Dispatch<React.SetStateAction<Task | null>>
  setLogs: (v: string[]) => void
  setScreenshots: React.Dispatch<React.SetStateAction<Record<string, string>>>
  setAgentMessages: (v: AgentMessage[]) => void
  setTodoItems: (v: TodoItem[]) => void
  setSelectedAnswers: (v: Record<string, string>) => void
  setOtherInputs: (v: Record<string, string>) => void
  setShowOtherInput: (v: Record<string, boolean>) => void
  setChatInput: (v: string) => void
  setChatAttachments: (v: StagedAttachment[]) => void
  setPendingQuestions: (v: PendingQuestions | null) => void
  setConversationItems: (v: ConversationItem[]) => void
  setSteps: (v: TaskStep[]) => void
}

/**
 * Load a task's full detail (task, pending question, messages, steps,
 * screenshots) into App state when its route param appears.
 *
 * Stale-response guard: selecting a task fans out several awaited
 * fetches. If the user switches to another task before they resolve, the
 * slower load's responses would otherwise land LAST and clobber the newer
 * selection — the detail pane snaps back to the task you navigated away
 * from ("clicking the other task does nothing"), most visibly when
 * switching AWAY from a running task whose message/step load is heavy.
 * Each call claims the next `seqRef` value; after every await we bail
 * unless this load is still the current one. Mirrors the
 * inFlightTasksKeyRef / inFlightScheduleIdRef guards used elsewhere.
 */
export async function loadTaskById(taskId: string, deps: LoadTaskDeps): Promise<void> {
  const { api } = deps
  const seq = ++deps.seqRef.current
  const fresh = () => deps.seqRef.current === seq

  let fullTask: Task
  try { fullTask = (await api.get(`/api/tasks/${taskId}`)) as Task } catch { return }
  if (!fresh()) return
  deps.setSelectedTask(fullTask); deps.setLogs([]); deps.setScreenshots({}); deps.setAgentMessages([])
  if (fullTask.todos) { try { deps.setTodoItems(JSON.parse(fullTask.todos)) } catch { deps.setTodoItems([]) } } else { deps.setTodoItems([]) }
  deps.setSelectedAnswers({}); deps.setOtherInputs({}); deps.setShowOtherInput({}); deps.setChatInput(''); deps.setChatAttachments([])
  deps.setPendingQuestions(null)  // Clear immediately to avoid stale UI

  // Recover pending question if agent is waiting
  try {
    const q = (await api.get(`/api/tasks/${fullTask.id}/questions/pending`)) as { pending?: boolean } & PendingQuestions
    if (!fresh()) return
    if (q.pending) deps.setPendingQuestions({ request_id: q.request_id, questions: q.questions })
    else deps.setPendingQuestions(null)
  } catch { if (fresh()) deps.setPendingQuestions(null) }

  // Rebuild the conversation stream from persisted messages + task state.
  let convItems: ConversationItem[] = []
  try {
    const msgs = (await api.get(`/api/tasks/${taskId}/messages`)) as { role: string; content: string }[]
    if (!fresh()) return
    deps.setAgentMessages(msgs.map(m => ({ role: m.role, content: m.content })))
    convItems = buildConversationItems(msgs, fullTask)
  } catch { /* ignore */ }
  if (!fresh()) return
  deps.setConversationItems(convItems)

  const stepsData = (await api.get(`/api/tasks/${taskId}/steps`)) as TaskStep[]
  if (!fresh()) return
  deps.setSteps(stepsData)
  for (const s of stepsData) {
    if (!s.screenshot_id) continue
    try {
      const resp = await fetch(`/api/screenshots/${s.screenshot_id}`, { credentials: 'include' })
      if (resp.ok) {
        const blob = await resp.blob()
        const reader = new FileReader()
        reader.onload = () => {
          if (!fresh()) return
          const b64 = (reader.result as string).split(',')[1]
          deps.setScreenshots(prev => ({ ...prev, [s.id]: b64 }))
        }
        reader.readAsDataURL(blob)
      }
    } catch { /* ignore */ }
  }
}
