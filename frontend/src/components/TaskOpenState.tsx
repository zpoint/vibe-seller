import { useTranslation } from 'react-i18next'
import { isRequestTimeout } from '../lib/requestDeadline'

/**
 * The detail pane while a task is being opened (`error` null) or after
 * it failed to open — the reason, or that the server did not answer in
 * time, with Try again.
 */
export function TaskOpenState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const { t } = useTranslation()
  if (error == null) {
    return (
      <div data-testid="task-opening" className="flex items-center justify-center h-full gap-3 text-sm text-gray-500">
        <div className="w-5 h-5 rounded-full border-2 border-indigo-500 border-t-transparent animate-spin" />
        {t('tasks.openingTask')}
      </div>
    )
  }
  return (
    <div role="alert" data-testid="task-open-error" className="flex flex-col items-center justify-center h-full gap-3 px-6 text-center">
      <p className="text-sm text-red-600">
        {isRequestTimeout(error)
          ? t('tasks.openTimeout')
          : t('tasks.openFailed', { error: error instanceof Error ? error.message : String(error) })}
      </p>
      <button
        onClick={onRetry}
        className="px-3 py-1.5 text-sm text-gray-700 border border-gray-300 rounded-lg hover:bg-gray-100"
      >
        {t('tasks.tryAgain')}
      </button>
    </div>
  )
}

/** Shown above the conversation while an opened task's transcript loads. */
export function TranscriptLoading() {
  const { t } = useTranslation()
  return (
    <div data-testid="transcript-loading" className="flex items-center gap-2 mb-4 text-sm text-gray-500">
      <div className="w-4 h-4 rounded-full border-2 border-indigo-500 border-t-transparent animate-spin" />
      {t('tasks.loadingConversation')}
    </div>
  )
}
