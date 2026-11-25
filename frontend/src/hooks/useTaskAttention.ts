import { useEffect, useRef, useState } from 'react'
import { api } from '../api'
import type { WaitingTaskItem } from '../types'
import { ATTENTION_EVENT } from './useScheduleAttention'

/**
 * Tasks waiting on the user (GET /api/tasks/attention), the one that has
 * waited longest first: a question to answer, or a wait only a reply
 * ends.
 *
 * Refetched on mount and on ATTENTION_EVENT (useSSE fires it when a
 * question is asked or a task changes status — parked, answered, timed
 * out). State only changes when the content does.
 */
export function useTaskAttention(): WaitingTaskItem[] {
  const [items, setItems] = useState<WaitingTaskItem[]>([])
  const [nudge, setNudge] = useState(0)
  // Only the latest request may set state: a slow earlier response must
  // not bring back a task the user has just answered.
  const seq = useRef(0)
  const last = useRef('[]')

  useEffect(() => {
    const bump = () => setNudge(n => n + 1)
    window.addEventListener(ATTENTION_EVENT, bump)
    return () => window.removeEventListener(ATTENTION_EVENT, bump)
  }, [])

  useEffect(() => {
    const mine = ++seq.current
    api
      .get('/api/tasks/attention')
      .then((r: { waiting_tasks?: WaitingTaskItem[] } | null) => {
        if (mine !== seq.current) return
        const next = r?.waiting_tasks ?? []
        const key = JSON.stringify(next)
        if (key === last.current) return
        last.current = key
        setItems(next)
      })
      .catch(() => {
        /* non-fatal — the next event retries */
      })
  }, [nudge])

  return items
}
