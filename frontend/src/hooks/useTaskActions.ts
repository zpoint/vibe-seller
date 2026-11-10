import { useEffect, useRef } from 'react'
import { api } from '../api'
import { retryTask as retryTaskHandler } from '../handlers/retryTask'
import { continueTask as continueTaskHandler } from '../handlers/continueTask'
import { deleteTask as deleteTaskHandler } from '../handlers/deleteTask'
import type { Task } from '../types'

type Setter<T> = React.Dispatch<React.SetStateAction<T>>

export interface TaskActionDeps {
  selectedTask: Task | null
  profileId: string
  tasks: Task[]
  scheduleTasks: Task[]
  setTasks: Setter<Task[]>
  setSelectedTask: Setter<Task | null>
  setScheduleTasks: Setter<Task[]>
  /** Clear the open task's question state (pending question + answers). */
  clearQuestions: () => void
  /** Clear the open task's run detail (steps, logs, conversation, …). */
  clearDetail: () => void
}

/**
 * The open task's header actions (stop, retry, continue, delete), plus a
 * ref to the id of the task that is open NOW.
 *
 * A response can land after the user opened another task; anything it
 * would write to the open task's state is applied only while its own
 * task is still the open one (`isOpen`). Stop used to clear the pending
 * question of whatever task was open when its response arrived.
 *
 * Each action rejects when it did not go through, so the view can say so.
 */
export function useTaskActions(d: TaskActionDeps) {
  const openTaskIdRef = useRef<string | null>(d.selectedTask?.id ?? null)
  useEffect(() => { openTaskIdRef.current = d.selectedTask?.id ?? null }, [d.selectedTask?.id])
  const isOpen = (taskId: string) => openTaskIdRef.current === taskId

  const handlerDeps = {
    api, profileId: d.profileId,
    setTasks: d.setTasks, setSelectedTask: d.setSelectedTask, setScheduleTasks: d.setScheduleTasks,
  }

  const stopAgent = async () => {
    if (!d.selectedTask) return
    const id = d.selectedTask.id
    await api.post(`/api/tasks/${id}/agent/stop`)
    if (isOpen(id)) d.clearQuestions()
  }
  // Both handlers recover the row themselves on failure; they report the
  // error through onError, and these wrappers reject with it so the
  // task view can say the action did not go through.
  const retryTask = async (taskId: string) => {
    let failed: unknown = null
    await retryTaskHandler(taskId, {
      ...handlerDeps,
      onError: (e) => { failed = e },
      onCleared: () => { if (isOpen(taskId)) { d.clearDetail(); d.clearQuestions() } },
    })
    if (failed) throw failed
  }
  const continueTask = async (taskId: string) => {
    let failed: unknown = null
    await continueTaskHandler(taskId, { ...handlerDeps, onError: (e) => { failed = e } })
    if (failed) throw failed
  }
  const deleteTask = (taskId: string) =>
    deleteTaskHandler(taskId, {
      api, tasks: d.tasks, scheduleTasks: d.scheduleTasks, selectedTask: d.selectedTask,
      setTasks: d.setTasks, setScheduleTasks: d.setScheduleTasks,
      onSelectedCleared: () => {
        // The user may have opened another task meanwhile — leave it be.
        if (d.selectedTask && !isOpen(d.selectedTask.id)) return
        d.setSelectedTask(null)
        d.clearDetail(); d.clearQuestions()
      },
    })

  return { stopAgent, retryTask, continueTask, deleteTask, isOpen }
}
