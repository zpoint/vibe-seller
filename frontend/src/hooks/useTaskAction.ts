import { useCallback, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { isRequestTimeout } from '../lib/requestDeadline'

interface ActionState {
  /** Which action is in flight, e.g. 'stop'. */
  pending: string | null
  error: string | null
}

const IDLE: ActionState = { pending: null, error: null }

/**
 * Run a task-header action (stop, continue, retry, mode switch, run the
 * plan) so the click is never silent: the action is marked pending
 * while its request is in flight — its button disables, and a second
 * click is ignored — and a failure is kept as a message to show.
 *
 * Those actions used to `catch { /* ignore *\/ }`: against a server that
 * was down or not answering, the click did visibly nothing.
 *
 * State is kept PER TASK: an action still in flight on one task while
 * the user acts on another keeps its own pending flag, guard and error,
 * and shows them again when the user returns to its task.
 */
export function useTaskAction(taskId: string | null) {
  const { t } = useTranslation()
  const [byTask, setByTask] = useState<Record<string, ActionState>>({})
  // Tasks with an action in flight (one at a time per task).
  const inFlight = useRef(new Set<string>())

  const run = useCallback(async (key: string, fn: () => unknown) => {
    if (taskId == null || inFlight.current.has(taskId)) return
    const owner = taskId
    inFlight.current.add(owner)
    const put = (s: ActionState) => setByTask(m => ({ ...m, [owner]: s }))
    put({ pending: key, error: null })
    try {
      await fn()
      put(IDLE)
    } catch (e) {
      put({
        pending: null,
        error: isRequestTimeout(e)
          ? t('tasks.actionTimeout')
          : t('tasks.actionFailed', { error: e instanceof Error ? e.message : String(e) }),
      })
    } finally {
      inFlight.current.delete(owner)
    }
  }, [taskId, t])

  const mine = (taskId != null && byTask[taskId]) || IDLE
  return { run, pending: mine.pending, error: mine.error }
}
