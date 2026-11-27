import { useTranslation } from 'react-i18next'
import { taskErrorHelp } from '../lib/docsHelp'
import { DocsHelpLink } from './ui/DocsHelpLink'
import { settingsLinksIn } from '../lib/settingsLinks'
import type { SettingsTab } from '../views/SettingsView'
import { useIntegrationAttention } from '../hooks/useIntegrationAttention'

/** Why a task failed, under its header: the sentence for its error
 *  category (the raw error when it has none), the launcher download when
 *  WSL could not reach Ziniao, the docs section that says how to fix it
 *  when the docs have one, and — when the agent linked a Settings page
 *  (an expired workplace sign-in: #integrations) — a button to it. */
export function TaskErrorNote({
  error,
  category,
  onOpenSettings,
}: {
  error: string
  category?: string | null
  onOpenSettings?: (tab: SettingsTab, anchor?: string) => void
}) {
  const { t } = useTranslation()
  const help = taskErrorHelp(category, error)
  const { text, links } = settingsLinksIn(error)
  const platforms = useIntegrationAttention()
  // The agent may say "re-authorize" in prose without writing the link.
  // Offer it only for an enabled platform the server says is expired,
  // and only when this error actually names that platform.
  const named = platforms.find(item => {
    const names = item.title.split(/[()]/).map(n => n.trim()).filter(n => n.length > 1)
    return names.some(name => error.toLowerCase().includes(name.toLowerCase()))
  })
  if (named && !links.some(link => link.anchor === `cli-${named.key}`)) {
    links.push({ label: t('integrationBanner.action'), tab: 'integrations', anchor: `cli-${named.key}` })
  }
  return (
    <div data-testid="task-error" className="mt-2 text-sm text-red-600 bg-red-50 px-3 py-2 rounded whitespace-pre-line">
      {category ? t(`tasks.error_${category}`, text) : text}
      {onOpenSettings && links.map(link => (
        <button
          key={`${link.tab}${link.anchor ?? ''}`}
          type="button"
          onClick={() => onOpenSettings(link.tab, link.anchor)}
          data-testid={`task-error-settings-${link.tab}`}
          className="ml-2 inline-flex items-center gap-1 px-2 py-0.5 bg-indigo-600 text-white rounded text-xs hover:bg-indigo-700"
        >
          {link.label}
          <span aria-hidden="true">→</span>
        </button>
      ))}
      {error.includes('/api/ziniao/launcher') && (
        <a href="/api/ziniao/launcher" download className="ml-2 inline-block px-2 py-0.5 bg-indigo-600 text-white rounded text-xs hover:bg-indigo-700">{t('settings.ziniaoDownloadLauncher')}</a>
      )}
      {help && <DocsHelpLink topic={help} className="ml-2" />}
    </div>
  )
}
