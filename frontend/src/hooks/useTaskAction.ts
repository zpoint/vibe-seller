import { useCallback, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { isRequestTimeout } from '../lib/requestDeadline'

interface ActionState {
  taskId: string | null
  /** Which action is in flight, e.g. 'stop'. */
  pending: string | null
  error: string | null
}

/**
 * Run a task-header action (stop, continue, retry, mode switch, run the
 * plan) so the click is never silent: the action is marked pending
 * while its request is in flight — its button disables, and a second
 * click is ignored — and a failure is kept as a message to show.
 *
 * Those actions used to `catch { /* ignore *\/ }`: against a server that
 * was down or not answering, the click did visibly nothing.
 *
 * State is tagged with the task it belongs to, so an action that settles
 * after the user opened another task shows nothing there.
 */
export function useTaskAction(taskId: string | null) {
  const { t } = useTranslation()
  const [state, setState] = useState<ActionState>({ taskId: null, pending: null, error: null })
  // The task whose action is in flight (one at a time per task).
  const inFlight = useRef<string | null>(null)

  const run = useCallback(async (key: string, fn: () => unknown) => {
    const owner = taskId
    if (inFlight.current !== null && inFlight.current === owner) return
    inFlight.current = owner
    setState({ taskId: owner, pending: key, error: null })
    try {
      await fn()
      setState(s => (s.taskId === owner ? { ...s, pending: null } : s))
    } catch (e) {
      const error = isRequestTimeout(e)
        ? t('tasks.actionTimeout')
        : t('tasks.actionFailed', { error: e instanceof Error ? e.message : String(e) })
      setState(s => (s.taskId === owner ? { ...s, pending: null, error } : s))
    } finally {
      if (inFlight.current === owner) inFlight.current = null
    }
  }, [taskId, t])

  const mine = state.taskId === taskId
  return {
    run,
    pending: mine ? state.pending : null,
    error: mine ? state.error : null,
  }
}
