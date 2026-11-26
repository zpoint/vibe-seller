import { useTranslation } from 'react-i18next'
import { taskErrorHelp } from '../lib/docsHelp'
import { DocsHelpLink } from './ui/DocsHelpLink'

/** Why a task failed, under its header: the sentence for its error
 *  category (the raw error when it has none), the launcher download when
 *  WSL could not reach Ziniao, and the docs section that says how to fix
 *  it when the docs have one. */
export function TaskErrorNote({ error, category }: { error: string; category?: string | null }) {
  const { t } = useTranslation()
  const help = taskErrorHelp(category, error)
  return (
    <div data-testid="task-error" className="mt-2 text-sm text-red-600 bg-red-50 px-3 py-2 rounded whitespace-pre-line">
      {category ? t(`tasks.error_${category}`, error) : error}
      {error.includes('/api/ziniao/launcher') && (
        <a href="/api/ziniao/launcher" download className="ml-2 inline-block px-2 py-0.5 bg-indigo-600 text-white rounded text-xs hover:bg-indigo-700">{t('settings.ziniaoDownloadLauncher')}</a>
      )}
      {help && <DocsHelpLink topic={help} className="ml-2" />}
    </div>
  )
}
