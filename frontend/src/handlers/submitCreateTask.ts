/**
 * Handler for the "Create task" submit action.
 *
 * Extracted from App.tsx so the create-and-insert-into-list contract
 * can be unit-tested without rendering the whole tree. Two
 * properties this function pins:
 *
 *   1. **At-most-once insertion.** The backend emits `task_created`
 *      via SSE *before* the POST response returns (see
 *      app/routers/tasks.py — the ordering is load-bearing for
 *      cross-tab live list updates). On the originating tab the
 *      SSE event can therefore arrive while `await api.post(...)`
 *      is still in flight, prepending the task via useSSE. The
 *      POST-response branch must dedup by id before prepending,
 *      otherwise the list shows the task twice until refresh.
 *
 *   2. **Race-guard refetch.** `task_update` events for
 *      QUEUED / RUNNING can also fire before the POST resolves; if
 *      the row isn't in the list yet, useSSE's `map()` silently
 *      drops the patch. A follow-up GET reconciles the status so
 *      the row reflects reality.  See
 *      __tests__/sseCreateTaskRace.test.tsx for the original race.
 *
 * Anything else (selectedTask reset, scratch state clear, file
 * uploads) is incidental UI bookkeeping.
 */
import type { PendingFile, Task } from '../types'

export interface SubmitCreateTaskApi {
  post(url: string, body: unknown): Promise<Task>
  get(url: string): Promise<Task>
}

export interface SubmitCreateTaskDeps {
  api: SubmitCreateTaskApi
  storeId: string | null
  planMode: boolean
  setTasks: React.Dispatch<React.SetStateAction<Task[]>>
  setSelectedTask: React.Dispatch<React.SetStateAction<Task | null>>
  /** Called after the prepend so callers can clear scratch UI state. */
  onCreated?: (task: Task) => void
  /** Optional per-attachment uploader (UI runs the real fetch). */
  uploadAttachment?: (taskId: string, pf: PendingFile) => Promise<void>
  /** Launch a deferred task after its attachments are uploaded. */
  startTask?: (taskId: string) => Promise<void>
}

export interface SubmitCreateTaskInput {
  title: string
  description: string
  files: PendingFile[]
  platform?: string
  country?: string
}

export async function submitCreateTask(
  input: SubmitCreateTaskInput,
  deps: SubmitCreateTaskDeps,
): Promise<Task> {
  const hasFiles = input.files.length > 0
  // Defer the agent start when there are attachments: the files must be
  // uploaded into the workspace BEFORE the agent reads its prompt,
  // otherwise it starts with no image and asks "where is the image?".
  const task = await deps.api.post('/api/tasks', {
    store_id: deps.storeId,
    title: input.title,
    description: input.description || null,
    plan_mode: deps.planMode,
    platform: input.platform || null,
    country: input.country || null,
    defer_start: hasFiles,
  })

  // Dedup-on-prepend: SSE `task_created` may have arrived first
  // (see header comment).  Both insert sites must enforce the
  // at-most-once contract.
  deps.setTasks(prev =>
    prev.some(p => p.id === task.id) ? prev : [task, ...prev],
  )
  deps.setSelectedTask(task)
  deps.onCreated?.(task)  // close the modal immediately — don't block on uploads

  // Upload attachments, THEN launch. Runs in the background so the modal
  // closes at once; the deferred task only starts once its files are in
  // the workspace (agent-visible).
  if (hasFiles) {
    void (async () => {
      if (deps.uploadAttachment) {
        for (const pf of input.files) await deps.uploadAttachment(task.id, pf)
      }
      await deps.startTask?.(task.id)
    })().catch(() => { /* SSE / status will reflect any failure */ })
  }

  // Race-guard refetch — see header comment.
  deps.api
    .get(`/api/tasks/${task.id}`)
    .then((t: Task) => {
      deps.setTasks(prev =>
        prev.map(pt => (pt.id === t.id ? { ...pt, ...t } : pt)),
      )
      deps.setSelectedTask(prev =>
        prev && prev.id === t.id ? { ...prev, ...t } : prev,
      )
    })
    .catch(() => {
      /* non-fatal — SSE will reconcile */
    })

  return task
}
