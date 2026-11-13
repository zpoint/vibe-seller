import { useTranslation } from 'react-i18next'
import { applyFailure, applyFailureLabel } from '../lib/updateOutcome'
import type { UpdateStatus } from '../types'

const ACTIVE_STATES = ['downloading', 'ready', 'restarting']

/**
 * Sidebar footer entry for the in-app update — the always-visible
 * counterpart of the modal: progress while the new release downloads
 * in the background, accent-styled when it is ready to apply, and a
 * busy bar while the server restarts onto it. Clicking opens the
 * update modal. Renders nothing for installs that cannot update
 * themselves (no supported flow) or when no update is in flight.
 *
 * On the pip path this chip is the ONLY ambient signal: that update
 * applies itself when the server is idle, so no modal pops to ask.
 * The installer path pops, and its chip says "restart to update"
 * because nothing will happen until someone clicks.
 *
 * A failed or rolled-back apply is reported here too, once the machine
 * is no longer busy with a newer attempt — otherwise a server that came
 * back on the old version would look like nothing ever happened.
 */
export function UpdateChip({
  status,
  onOpen,
}: {
  status: UpdateStatus | null
  onOpen: () => void
}) {
  const { t } = useTranslation()
  if (!status?.supported) return null

  // An apply in flight outranks the previous one's failure: the state
  // the admin can act on is the current one, and the modal still shows
  // what went wrong last time.
  const active =
    !!status.version && ACTIVE_STATES.includes(status.state)
  const failed = active ? null : applyFailure(status.last_update)
  if (!active && !failed) return null

  const version = status.version ?? ''
  const pip = status.path === 'pip'
  const pct =
    status.state === 'downloading' &&
    status.platform === 'windows' &&
    status.total_bytes &&
    status.downloaded_bytes !== null
      ? Math.min(100, Math.floor((status.downloaded_bytes / status.total_bytes) * 100))
      : null

  let label: string
  if (failed) {
    label = applyFailureLabel(t, failed)
  } else if (status.state === 'ready') {
    label = pip
      ? t('update.chipReadyPip', { version })
      : t('update.chipReadyInstaller', { version })
  } else if (status.state === 'restarting') {
    label = t('update.chipRestarting', { version })
  } else {
    label =
      pct !== null
        ? t('update.chipDownloadingPct', { version, pct })
        : t('update.chipDownloadingBusy', { version })
  }

  return (
    <button
      type="button"
      onClick={onOpen}
      data-testid="update-chip"
      className={`mx-3 mb-2 rounded-lg border px-2.5 py-2 text-left text-xs transition-colors ${
        status.state === 'ready'
          ? 'border-indigo-200 bg-indigo-50 font-medium text-indigo-700 hover:bg-indigo-100'
          : failed
            ? 'border-red-200 bg-red-50 text-red-700 hover:bg-red-100'
            : 'border-gray-200 bg-white text-gray-600 hover:bg-gray-50'
      }`}
    >
      <span>{label}</span>
      {active && status.state !== 'ready' && (
        <span className="mt-1.5 block h-1 w-full overflow-hidden rounded-full bg-gray-100">
          {pct !== null ? (
            <span
              className="block h-full rounded-full bg-indigo-600"
              style={{ width: `${pct}%` }}
            />
          ) : (
            <span className="block h-full w-1/3 animate-pulse rounded-full bg-indigo-400" />
          )}
        </span>
      )}
    </button>
  )
}
