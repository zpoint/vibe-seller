import { useTranslation } from 'react-i18next'
import { badgeStatus } from '../../lib/taskBadge'
import type { BadgeTask } from '../../lib/taskBadge'

export function StatusBadge({ task }: { task: BadgeTask }) {
  const { t } = useTranslation()
  const status = badgeStatus(task)
  const colors: Record<string, string> = {
    pending: 'bg-gray-200 text-gray-700',
    designing: 'bg-indigo-100 text-indigo-700',
    // Same look as a schedule plan waiting for approval (ScheduleList).
    pending_approval: 'bg-amber-200 text-amber-900',
    running: 'bg-indigo-100 text-indigo-700',
    completed: 'bg-green-100 text-green-700',
    failed: 'bg-red-100 text-red-700',
    queued: 'bg-yellow-100 text-yellow-700',
    waiting: 'bg-amber-100 text-amber-800',
  }
  return (
    <span data-testid="task-status-badge" className={`px-2 py-0.5 rounded-full text-xs font-medium whitespace-nowrap shrink-0 ${colors[status] || 'bg-gray-200 text-gray-700'}`}>
      {t(`status.${status}`) || status}
    </span>
  )
}
