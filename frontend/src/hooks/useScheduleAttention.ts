import { useEffect, useRef, useState } from 'react'
import { api } from '../api'
import type { PlanApprovalItem, PlannerQuestionItem, Schedule } from '../types'

/** Fired by useSSE when something that may change "what waits for the
 * user" happened (a question was asked, a task changed status). */
export const ATTENTION_EVENT = 'vs-attention-changed'

export interface ScheduleAttention {
  approvals: PlanApprovalItem[]
  questions: PlannerQuestionItem[]
}

const EMPTY: ScheduleAttention = { approvals: [], questions: [] }

/**
 * What waits for the user on schedules (GET /api/schedules/attention):
 * plans awaiting approval (oldest first) and planners waiting on an
 * answer.
 *
 * Refetched on mount, whenever `schedules` is reloaded (the SSE plan
 * events and approve / request changes reload it), and on
 * ATTENTION_EVENT (a question asked, a task status change). State only
 * changes when the content does, so a refetch that finds nothing new
 * re-renders nothing.
 */
export function useScheduleAttention(schedules: Schedule[]): ScheduleAttention {
  const [attention, setAttention] = useState<ScheduleAttention>(EMPTY)
  const [nudge, setNudge] = useState(0)
  // Only the latest request may set state: a slow earlier response
  // must not bring back a plan the user has just approved.
  const seq = useRef(0)
  const last = useRef(JSON.stringify(EMPTY))

  useEffect(() => {
    const bump = () => setNudge(n => n + 1)
    window.addEventListener(ATTENTION_EVENT, bump)
    return () => window.removeEventListener(ATTENTION_EVENT, bump)
  }, [])

  useEffect(() => {
    const mine = ++seq.current
    api
      .get('/api/schedules/attention')
      .then((r: { plan_approvals?: PlanApprovalItem[]; planner_questions?: PlannerQuestionItem[] } | null) => {
        if (mine !== seq.current) return
        const approvals = [...(r?.plan_approvals ?? [])]
        approvals.sort((a, b) => a.proposed_at.localeCompare(b.proposed_at))
        const next = { approvals, questions: r?.planner_questions ?? [] }
        const key = JSON.stringify(next)
        if (key === last.current) return
        last.current = key
        setAttention(next)
      })
      .catch(() => {
        /* non-fatal — the next reload retries */
      })
  }, [schedules, nudge])

  return attention
}
