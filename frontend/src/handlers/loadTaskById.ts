import { buildConversationItems } from '../lib/conversation'
import type { Task, TaskStep, TodoItem, AgentMessage, ConversationItem, StagedAttachment, ImageModelOption } from '../types'

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
  // Switch the selection AND clear ALL of the previous task's detail up
  // front — conversation and steps included. On a high-latency link the
  // follow-up fetches take a moment; without clearing here the OLD task's
  // conversation/steps linger under the new header, so the switch reads
  // as "nothing happened" until the new data lands. Clear now → the pane
  // flips to the new task immediately and fills in as data arrives.
  deps.setSelectedTask(fullTask)
  deps.setLogs([]); deps.setScreenshots({}); deps.setAgentMessages([])
  deps.setConversationItems([]); deps.setSteps([])
  if (fullTask.todos) { try { deps.setTodoItems(JSON.parse(fullTask.todos)) } catch { deps.setTodoItems([]) } } else { deps.setTodoItems([]) }
  deps.setSelectedAnswers({}); deps.setOtherInputs({}); deps.setShowOtherInput({}); deps.setChatInput(''); deps.setChatAttachments([])
  deps.setPendingQuestions(null)  // Clear immediately to avoid stale UI

  // Fetch the detail pieces CONCURRENTLY rather than in series — on a
  // slow link four sequential round-trips (question + messages + steps)
  // stack up into a visible delay; in parallel it is a single round-trip.
  type PendingImage = {
    pending?: boolean; request_id: string; prompt?: string; model?: string
    models?: ImageModelOption[]; reference_images?: string[]; output_name?: string; kind?: string
  }
  const [q, msgs, stepsData, pendingImg] = await Promise.all([
    api.get(`/api/tasks/${fullTask.id}/questions/pending`).catch(() => null) as Promise<({ pending?: boolean } & PendingQuestions) | null>,
    api.get(`/api/tasks/${taskId}/messages`).catch(() => []) as Promise<{ role: string; content: string }[]>,
    api.get(`/api/tasks/${taskId}/steps`).catch(() => []) as Promise<TaskStep[]>,
    api.get(`/api/tasks/${taskId}/image/pending`).catch(() => null) as Promise<PendingImage | null>,
  ])
  if (!fresh()) return

  deps.setPendingQuestions(q?.pending ? { request_id: q.request_id, questions: q.questions } : null)
  deps.setAgentMessages(msgs.map(m => ({ role: m.role, content: m.content })))
  const convItems = buildConversationItems(msgs, fullTask)
  // Re-render a still-pending image-confirm card (the SSE image_request
  // fired before this client connected — recover it so the task isn't
  // stuck waiting on a confirm the UI never showed).
  if (pendingImg?.pending) {
    convItems.push({
      id: `imgreq-${pendingImg.request_id}`,
      type: 'image_request',
      timestamp: new Date().toISOString(),
      imageRequest: {
        requestId: pendingImg.request_id,
        prompt: pendingImg.prompt || '',
        model: pendingImg.model || 'nano-banana-pro',
        models: pendingImg.models || [],
        referenceImages: pendingImg.reference_images || [],
        outputName: pendingImg.output_name,
        kind: pendingImg.kind,
        resolved: false,
      },
    })
  }
  deps.setConversationItems(convItems)
  deps.setSteps(stepsData)

  for (const s of stepsData) {
    // Bail the whole loop the moment a newer selection supersedes this
    // one — otherwise a stale load keeps fetching screenshots one by one,
    // hogging the browser's connection pool and delaying the NEW task's
    // requests (the "switch releases after a few seconds" symptom).
    if (!fresh()) return
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
