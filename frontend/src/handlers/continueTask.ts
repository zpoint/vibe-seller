/**
 * Handler for the "Continue" (继续) button on a failed / completed task.
 *
 * NON-destructive resume, in contrast to `retryTask` (which wipes the
 * plan / error / history and restarts from scratch). Continue behaves
 * exactly like the user typing "try again now" into the task's chat box:
 * it POSTs `/api/tasks/{id}/messages`, which the backend handles by
 * RESUMING the same task with its full context (reusing the CLI session
 * when present, else rebuilding from the saved conversation + plan) and
 * clearing only stale run-scoped error state. Work already on disk
 * (e.g. an audit's cached TSVs) is reused rather than redone.
 *
 * The contract mirrors `retryTask`'s SSE race-guard, but the optimistic
 * patch **only** advances `status` — it must NOT clear `error` / `plan`
 * / `plan_history`, so the conversation and plan stay visible while the
 * resumed run streams in. The backend clears the stale error itself and
 * the post-POST refetch/merge reconciles the rest.
 */
import type { Task } from '../types'

/** Canned message the Continue button sends — resume, don't restart. */
export const CONTINUE_MESSAGE =
  '继续之前的任务，现在重试；复用磁盘上已有的进度和数据，不要从头重写。'

export interface ContinueTaskApi {
  post(url: string, body: unknown): Promise<unknown>
  get(url: string): Promise<Task>
}

export interface ContinueTaskDeps {
  api: ContinueTaskApi
  profileId: string
  setTasks: React.Dispatch<React.SetStateAction<Task[]>>
  setSelectedTask: React.Dispatch<React.SetStateAction<Task | null>>
  setScheduleTasks: React.Dispatch<React.SetStateAction<Task[]>>
}

export async function continueTask(
  taskId: string,
  deps: ContinueTaskDeps,
): Promise<void> {
  // Optimistic status bump ONLY — no context clearing. Fires before the
  // await so SSE `task_update` events (running → …) that arrive during
  // the POST round-trip can validly progress the row without being
  // clobbered by a post-await write.
  const patch = { status: 'running' as const }
  deps.setTasks(prev =>
    prev.map(t => (t.id === taskId ? { ...t, ...patch } : t)),
  )
  deps.setSelectedTask(prev =>
    prev && prev.id === taskId ? { ...prev, ...patch } : prev,
  )
  deps.setScheduleTasks(prev =>
    prev.map(t => (t.id === taskId ? { ...t, ...patch } : t)),
  )

  try {
    await deps.api.post(`/api/tasks/${taskId}/messages`, {
      content: CONTINUE_MESSAGE,
      profile_id: deps.profileId,
    })
  } catch {
    // POST failed — refetch so the row reflects the server's real
    // state (typically still failed / completed) instead of being
    // stuck optimistically at running.
    deps.api
      .get(`/api/tasks/${taskId}`)
      .then(mergeTask(deps))
      .catch(() => {
        /* both POST and GET failed; next refresh fixes it */
      })
    return
  }

  // Race-guard refetch: pick up whatever status the server has now
  // without overwriting progress the SSE handler already applied.
  deps.api
    .get(`/api/tasks/${taskId}`)
    .then(mergeTask(deps))
    .catch(() => {
      /* non-fatal — SSE continues to drive state */
    })
}

function mergeTask(deps: ContinueTaskDeps) {
  return (t: Task) => {
    deps.setTasks(prev =>
      prev.map(pt => (pt.id === t.id ? { ...pt, ...t } : pt)),
    )
    deps.setSelectedTask(prev =>
      prev && prev.id === t.id ? { ...prev, ...t } : prev,
    )
    deps.setScheduleTasks(prev =>
      prev.map(pt => (pt.id === t.id ? { ...pt, ...t } : pt)),
    )
  }
}
