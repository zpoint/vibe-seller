import { useTranslation } from 'react-i18next'

export function EventStatusBadge({ status }: { status: string }) {
  const { t } = useTranslation()
  const colors: Record<string, string> = {
    draft: 'bg-gray-200 text-gray-700',
    open: 'bg-indigo-100 text-indigo-700',
    in_progress: 'bg-indigo-100 text-indigo-700',
    waiting: 'bg-amber-100 text-amber-700',
    resolved: 'bg-green-100 text-green-700',
    closed: 'bg-gray-300 text-gray-600',
    dismissed: 'bg-red-100 text-red-600',
  }
  return (
    <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${colors[status] || 'bg-gray-200 text-gray-700'}`}>
      {t(`status.${status}`) || status.replace('_', ' ')}
    </span>
  )
}
