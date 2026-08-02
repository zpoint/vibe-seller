import { useEffect, useState } from 'react'
import { api } from '../api'
import { submitAuditDecisions } from '../handlers/submitAuditDecisions'
import type { AdDeclaration } from '../lib/adAudit/declaration'
import type { DecisionSubmission } from '../lib/adAudit/types'
import type { Task } from '../types'

interface Fetched {
  taskId?: string
  declarations: AdDeclaration[]
}

interface Deps {
  selectedTask: Task | null
  selectedProfileId: string
  setSelectedTask: React.Dispatch<React.SetStateAction<Task | null>>
  setTasks: React.Dispatch<React.SetStateAction<Task[]>>
  onCloseAudit?: () => void
}

/**
 * Everything the ad-review console needs from the open task.
 *
 * `declarations` is what each phase of the task declared it was for — it
 * decides which result gets a console and how much of it is actionable.
 * That used to be a regex over the agent's own report prose, which
 * handed a two-campaign task a console listing every campaign in the
 * store.
 *
 * Declarations re-read on status change as well as task change, because
 * one is made mid-run: the console must not depend on the task having
 * been reopened afterwards. The fetched task id is kept alongside the
 * data and compared on read rather than cleared when the task changes —
 * clearing would mean a synchronous setState inside the effect, and
 * would still show the previous task's declarations for a frame.
 */
export function useAuditConsole({
  selectedTask,
  selectedProfileId,
  setSelectedTask,
  setTasks,
  onCloseAudit,
}: Deps) {
  const [fetched, setFetched] = useState<Fetched>({ declarations: [] })
  const [submitting, setSubmitting] = useState(false)
  const taskId = selectedTask?.id
  const taskStatus = selectedTask?.status

  useEffect(() => {
    if (!taskId) return
    let cancelled = false
    api
      .get(`/api/tasks/${taskId}/ad-declarations`)
      .then((d) => {
        if (cancelled) return
        setFetched({ taskId, declarations: Array.isArray(d) ? d : [] })
      })
      .catch(() => {
        if (!cancelled) setFetched({ taskId, declarations: [] })
      })
    return () => {
      cancelled = true
    }
  }, [taskId, taskStatus])

  const submit = async (submission: DecisionSubmission) => {
    if (!selectedTask) return
    setSubmitting(true)
    try {
      await submitAuditDecisions(selectedTask.id, submission, {
        api,
        profileId: selectedProfileId,
        onOptimisticStatus: (status) => {
          setSelectedTask((prev) =>
            prev && prev.id === selectedTask.id ? { ...prev, status } : prev,
          )
          setTasks((prev) =>
            prev.map((t) =>
              t.id === selectedTask.id ? { ...t, status } : t,
            ),
          )
        },
      })
      // The decisions are with the agent now; close the console so the
      // task's stream is what you watch.
      onCloseAudit?.()
    } finally {
      setSubmitting(false)
    }
  }

  return {
    declarations: fetched.taskId === taskId ? fetched.declarations : [],
    submitting,
    submit,
  }
}
