import { useEffect, useRef, useState } from 'react'
import { api } from '../api'
import type { PlanApprovalItem, Schedule } from '../types'

/**
 * Schedules whose plan waits for the user's approval, oldest first
 * (GET /api/schedules/attention).
 *
 * Refetched on mount and whenever `schedules` is reloaded — the SSE
 * plan events (`schedule_plan_*`) reload the schedule list, and so do
 * approve / request changes, so one dependency covers every path.
 */
export function useScheduleAttention(schedules: Schedule[]) {
  const [approvals, setApprovals] = useState<PlanApprovalItem[]>([])
  // Only the latest request may set state: a slow earlier response
  // must not bring back a plan the user has just approved.
  const seq = useRef(0)

  useEffect(() => {
    const mine = ++seq.current
    api
      .get('/api/schedules/attention')
      .then((r: { plan_approvals?: PlanApprovalItem[] } | null) => {
        if (mine !== seq.current) return
        const items = [...(r?.plan_approvals ?? [])]
        items.sort((a, b) => a.proposed_at.localeCompare(b.proposed_at))
        setApprovals(items)
      })
      .catch(() => {
        /* non-fatal — the next reload retries */
      })
  }, [schedules])

  return approvals
}
