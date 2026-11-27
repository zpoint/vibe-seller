import { useTranslation } from 'react-i18next'
import { DocsHelpLink } from '../ui/DocsHelpLink'
import type { CliPermission } from '../../hooks/useCliPermissions'

const PILL: Record<CliPermission['state'], string> = {
  ok: 'bg-green-50 text-green-800 border-green-200',
  expired: 'bg-amber-50 text-amber-900 border-amber-300',
  unknown: 'bg-gray-100 text-gray-600 border-gray-200',
}

/** The row's list of capabilities: each one's name and state, and —
 *  when any expired — what to do about it. */
export function CliPermissions({
  cardKey,
  items,
  loading,
}: {
  cardKey: string
  items: CliPermission[] | null
  loading: boolean
}) {
  const { t } = useTranslation()
  if (!items) {
    return loading ? (
      <p className="text-xs text-gray-500" data-testid={`cli-permissions-checking-${cardKey}`}>
        {t('integrations.cli.permissions.checking')}
      </p>
    ) : null
  }
  const expired = items.filter(p => p.state === 'expired')
  return (
    <div className="space-y-1.5" data-testid={`cli-permissions-${cardKey}`}>
      <ul className="flex flex-wrap gap-1.5">
        {items.map(p => (
          <li
            key={p.key}
            data-testid={`cli-permission-${cardKey}-${p.key}`}
            data-state={p.state}
            className={`inline-flex items-center gap-1 rounded border px-2 py-0.5 text-xs ${PILL[p.state]}`}
          >
            <span className="font-medium">{t(`integrations.cli.permissions.name.${p.key}`, { defaultValue: p.key })}</span>
            <span>{t(`integrations.cli.permissions.state.${p.state}`)}</span>
            {p.state === 'expired' && p.renew_url && (
              <a
                href={p.renew_url}
                target="_blank"
                rel="noopener noreferrer"
                className="underline underline-offset-2 hover:text-amber-700"
                data-testid={`cli-permission-renew-${cardKey}-${p.key}`}
              >
                {t('integrations.cli.permissions.renewOne')}
              </a>
            )}
          </li>
        ))}
      </ul>
      {expired.length > 0 && (
        <p className="flex flex-wrap items-center gap-x-1 text-xs text-amber-800" data-testid={`cli-permissions-hint-${cardKey}`}>
          {t('integrations.cli.permissions.expiredHint')}
          <DocsHelpLink topic="wecomPermissionExpired" />
        </p>
      )}
    </div>
  )
}
