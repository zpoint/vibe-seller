import { useTranslation } from 'react-i18next'

export function ExecutionSeparator() {
  const { t } = useTranslation()
  return (
    <div className="flex items-center gap-3 my-4">
      <div className="flex-1 border-t border-dashed border-gray-300" />
      <span className="text-xs text-gray-400 uppercase tracking-wide">
        {t('tasks.executionStarted')}
      </span>
      <div className="flex-1 border-t border-dashed border-gray-300" />
    </div>
  )
}
