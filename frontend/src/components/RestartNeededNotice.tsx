/**
 * "A synced skill changed its gate code — restart to load it."
 *
 * Gates load once per process, so until a restart the change is not
 * live. This is the only place that says so; it is deliberately small
 * and non-blocking.
 */
import { useTranslation } from 'react-i18next'

export function RestartNeededNotice({
  needsRestart,
}: {
  needsRestart?: boolean | null
}) {
  const { t } = useTranslation()
  if (!needsRestart) return null
  return (
    <p
      className="px-3 pb-1 text-[10px] text-amber-600 leading-tight"
      data-testid="restart-needed"
    >
      {t('workspace.restartNeeded')}
    </p>
  )
}
