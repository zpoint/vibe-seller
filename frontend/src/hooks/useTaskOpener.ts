import { useRef, useState } from 'react'
import { api } from '../api'
import { loadTaskById, type LoadTaskDeps } from '../handlers/loadTaskById'

/** The task the route asks for, while it is loading (error null) or
 *  after it failed to load. */
export type TaskLoad = { taskId: string; error: unknown } | null

type SetterDeps = Omit<LoadTaskDeps, 'api' | 'seqRef' | 'onLoaded' | 'onLoadError'>

/**
 * Opens tasks for the route: runs loadTaskById and tracks the load, so
 * TasksView shows a pending or error state for the task being opened
 * instead of whatever was open before.
 */
export function useTaskOpener(setters: SetterDeps) {
  // Monotonic token for loadTaskById: only the latest selection applies
  // its fetched state (stale-response-wins guard for fast task switches).
  const seqRef = useRef(0)
  const [taskLoad, setTaskLoad] = useState<TaskLoad>(null)

  const open = (taskId: string) => {
    setTaskLoad({ taskId, error: null })
    return loadTaskById(taskId, {
      ...setters,
      api, seqRef,
      onLoaded: () => setTaskLoad(null),
      onLoadError: (error) => setTaskLoad({ taskId, error }),
    })
  }

  /** Retire any load still in flight (it would switch the pane away)
   *  and drop a failed one's error. */
  const retire = () => {
    seqRef.current++
    setTaskLoad(null)
  }

  const retry = () => { if (taskLoad) open(taskLoad.taskId) }

  return { taskLoad, open, retire, retry }
}
