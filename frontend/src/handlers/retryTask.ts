/**
 * Handler for the "Retry" / "Re-run" button.
 *
 * Extracted from App.tsx so the click → state-update sequence can
 * be unit-tested without rendering the whole tree. The contract:
 *
 *   1. Apply the "clearing" patch (`status: 'pending'`, error /
 *      plan / plan_history wiped, profile id swapped) **before**
 *      the await — so the user sees the failed/completed task
 *      flip to pending immediately on click.
 *   2. POST `/api/tasks/{id}/retry` — backend awaits
 *      `schedule_or_run` and emits `task_update` SSE events for
 *      `pending → queued → running` *while the request is in
 *      flight*. The SSE handler in `useSSE` patches `tasks` /
 *      `selectedTask` to those statuses as the events arrive.
 *   3. After the POST resolves, refetch the task and merge — the
 *      merge is what makes step 2's SSE updates survive. Without
 *      the merge (the previous bug), the post-await write
 *      clobbered the progress with `status: 'pending'`.
 *
 * Same race shape as the create-task path; see
 * __tests__/sseCreateTaskRace.test.tsx for the equivalent guard
 * on `submitCreateTask`.
 */
import type { Task } from '../types'

export interface RetryTaskApi {
  post(url: string, body: unknown): Promise<unknown>
  get(url: string): Promise<Task>
}

export interface RetryTaskDeps {
  api: RetryTaskApi
  profileId: string
  setTasks: React.Dispatch<React.SetStateAction<Task[]>>
  setSelectedTask: React.Dispatch<React.SetStateAction<Task | null>>
  setScheduleTasks: React.Dispatch<React.SetStateAction<Task[]>>
  /** Called after the optimistic patch so the caller can clear
   *  scratch UI state (steps, screenshots, conversation, …). */
  onCleared?: () => void
}

export async function retryTask(
  taskId: string,
  deps: RetryTaskDeps,
): Promise<void> {
  // Optimistic clear — fires synchronously *before* the await so
  // SSE events that arrive during the POST round-trip can validly
  // progress the task forward. Without this ordering, a post-await
  // write would clobber `pending → queued → running` SSE updates
  // (race seen in production: list stuck at `pending` while detail
  // showed `running`).
  const clearPatch = {
    status: 'pending' as const,
    error: null,
    plan: null,
    plan_history: null,
    ai_profile_id: deps.profileId,
  }
  deps.setTasks(prev =>
    prev.map(t => (t.id === taskId ? { ...t, ...clearPatch } : t)),
  )
  deps.setSelectedTask(prev =>
    prev && prev.id === taskId ? { ...prev, ...clearPatch } : prev,
  )
  deps.setScheduleTasks(prev =>
    prev.map(t => (t.id === taskId ? { ...t, ...clearPatch } : t)),
  )
  deps.onCleared?.()

  try {
    await deps.api.post(`/api/tasks/${taskId}/retry`, {
      profile_id: deps.profileId,
    })
  } catch {
    // POST failed (network / auth / 500). The optimistic clear
    // would otherwise leave the row stuck at `pending` forever —
    // no agent started, so no SSE will ever land. Refetch the
    // server's actual state so the row reflects reality
    // (typically still `failed` / `completed` from before retry).
    // Fire-and-forget; if the GET also fails the row stays
    // optimistic, which is the prior behavior.
    deps.api
      .get(`/api/tasks/${taskId}`)
      .then((t: Task) => {
        deps.setTasks(prev =>
          prev.map(pt => (pt.id === t.id ? { ...pt, ...t } : pt)),
        )
        deps.setSelectedTask(prev =>
          prev && prev.id === t.id ? { ...prev, ...t } : prev,
        )
        deps.setScheduleTasks(prev =>
          prev.map(pt => (pt.id === t.id ? { ...pt, ...t } : pt)),
        )
      })
      .catch(() => {
        /* both POST and GET failed; let the next refresh fix it */
      })
    return
  }

  // Race-guard refetch (success path): by the time POST resolves,
  // the backend has typically already committed
  // `pending → queued → running` and emitted task_update SSE for
  // each. The merge here picks up whatever status the server has
  // *now*, without overwriting progress the SSE handler already
  // applied.
  deps.api
    .get(`/api/tasks/${taskId}`)
    .then((t: Task) => {
      deps.setTasks(prev =>
        prev.map(pt => (pt.id === t.id ? { ...pt, ...t } : pt)),
      )
      deps.setSelectedTask(prev =>
        prev && prev.id === t.id ? { ...prev, ...t } : prev,
      )
      deps.setScheduleTasks(prev =>
        prev.map(pt => (pt.id === t.id ? { ...pt, ...t } : pt)),
      )
    })
    .catch(() => {
      /* non-fatal — SSE will continue to drive state */
    })
}
