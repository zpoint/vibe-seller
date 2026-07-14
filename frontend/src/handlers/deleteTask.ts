import i18n from '../i18n'
import type { Task } from '../types'

export interface DeleteTaskApi {
  get(url: string): Promise<Task[]>
  del(url: string): Promise<unknown>
}

export interface DeleteTaskDeps {
  api: DeleteTaskApi
  tasks: Task[]
  scheduleTasks: Task[]
  selectedTask: Task | null
  setTasks: React.Dispatch<React.SetStateAction<Task[]>>
  setScheduleTasks: React.Dispatch<React.SetStateAction<Task[]>>
  onSelectedCleared: () => void
}

/**
 * Delete a task and locally drop it plus any descendants (the server
 * cascade-deletes children but doesn't push an SSE delete for each), so
 * the tree disappears immediately without a refetch.
 */
export async function deleteTask(
  taskId: string,
  deps: DeleteTaskDeps,
): Promise<void> {
  let childCount = 0
  try {
    const kids = await deps.api.get(
      `/api/tasks?parent_task_id=${encodeURIComponent(taskId)}`,
    )
    childCount = Array.isArray(kids) ? kids.length : 0
  } catch { /* ignore — fall back to plain confirm */ }
  const message = childCount > 0
    ? i18n.t('tasks.deleteConfirmCascade', { count: childCount })
    : i18n.t('tasks.deleteConfirm')
  if (!confirm(message)) return
  try {
    await deps.api.del(`/api/tasks/${taskId}`)
    // BFS over known tasks to collect the deleted node + descendants.
    const dropIds = new Set<string>([taskId])
    let frontier = [taskId]
    while (frontier.length) {
      const next: string[] = []
      for (const id of frontier) {
        for (const t2 of deps.tasks) if (t2.parent_task_id === id) next.push(t2.id)
        for (const t2 of deps.scheduleTasks) if (t2.parent_task_id === id) next.push(t2.id)
      }
      // Filter to unvisited BEFORE adding to dropIds — otherwise every
      // BFS frontier collapses to [] after the first hop and we never
      // reach grandchildren.
      const unvisited = next.filter(id => !dropIds.has(id))
      unvisited.forEach(id => dropIds.add(id))
      frontier = unvisited
    }
    deps.setTasks(prev => prev.filter(x => !dropIds.has(x.id)))
    deps.setScheduleTasks(prev => prev.filter(x => !dropIds.has(x.id)))
    if (deps.selectedTask && dropIds.has(deps.selectedTask.id)) {
      deps.onSelectedCleared()
    }
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    if (msg !== 'unauthorized') alert(msg)
  }
}
