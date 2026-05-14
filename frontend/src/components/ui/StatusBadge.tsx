import { useTranslation } from 'react-i18next'

export function StatusBadge({ status }: { status: string }) {
  const { t } = useTranslation()
  const colors: Record<string, string> = {
    pending: 'bg-gray-200 text-gray-700',
    designing: 'bg-purple-100 text-purple-700',
    planned: 'bg-indigo-100 text-indigo-700',
    running: 'bg-blue-100 text-blue-700',
    completed: 'bg-green-100 text-green-700',
    failed: 'bg-red-100 text-red-700',
    queued: 'bg-yellow-100 text-yellow-700',
    waiting: 'bg-amber-100 text-amber-800',
  }
  return (
    <span className={`px-2 py-0.5 rounded-full text-xs font-medium whitespace-nowrap shrink-0 ${colors[status] || 'bg-gray-200 text-gray-700'}`}>
      {t(`status.${status}`) || status}
    </span>
  )
}
