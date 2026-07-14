import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { api } from '../api'
import { StatusBadge } from './ui'
import type { Task, Store } from '../types'

/** Child tasks of a parent, polled every 10s (no second SSE stream). */
export function SubtaskList({ parentTaskId, onSelect, stores }: {
  parentTaskId: string
  onSelect: (t: Task) => void
  stores: Store[]
}) {
  const { t } = useTranslation()
  const [children, setChildren] = useState<Task[]>([])

  useEffect(() => {
    let cancelled = false
    const fetchChildren = () => {
      api.get(`/api/tasks?parent_task_id=${encodeURIComponent(parentTaskId)}`).then((data: Task[]) => {
        if (!cancelled) setChildren(data)
      }).catch(() => {})
    }
    fetchChildren()
    // Poll every 10s for status updates instead of opening a second SSE
    const interval = setInterval(fetchChildren, 10000)
    return () => { cancelled = true; clearInterval(interval) }
  }, [parentTaskId])

  if (children.length === 0) return null

  const storeName = (sid: string | null) => {
    if (!sid) return ''
    return stores.find(s => s.id === sid)?.name || sid.slice(0, 8)
  }

  return (
    <div className="mt-2">
      <div className="text-xs font-medium text-gray-500 mb-1">{t('tasks.subtasks', 'Subtasks')} ({children.length})</div>
      <div className="space-y-1">
        {children.map(child => (
          <button
            key={child.id}
            onClick={() => onSelect(child)}
            className="w-full text-left px-2 py-1.5 text-xs rounded border border-gray-200 hover:bg-gray-50 flex items-center gap-2"
          >
            <StatusBadge status={child.status} />
            <span className="truncate flex-1">{child.title}</span>
            {child.store_id && <span className="text-gray-400 text-[10px]">{storeName(child.store_id)}</span>}
          </button>
        ))}
      </div>
    </div>
  )
}
