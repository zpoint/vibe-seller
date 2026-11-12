import { useTranslation } from 'react-i18next'
import type { UpdateStatus } from '../types'

/**
 * Sidebar footer entry for the in-app update — the always-visible
 * counterpart of the modal: progress while the new release downloads
 * in the background, accent-styled when it is ready to apply, and a
 * busy bar while the server restarts onto it. Clicking opens the
 * update modal. Renders nothing for installs that cannot update
 * themselves (no supported flow) or when no update is in flight.
 */
export function UpdateChip({
  status,
  onOpen,
}: {
  status: UpdateStatus | null
  onOpen: () => void
}) {
  const { t } = useTranslation()
  if (!status?.supported || !status.version) return null
  if (!['downloading', 'ready', 'restarting'].includes(status.state)) return null

  const version = status.version
  const pct =
    status.state === 'downloading' &&
    status.platform === 'windows' &&
    status.total_bytes &&
    status.downloaded_bytes !== null
      ? Math.min(100, Math.floor((status.downloaded_bytes / status.total_bytes) * 100))
      : null

  const label =
    status.state === 'ready'
      ? t('update.chipReady', { version })
      : status.state === 'restarting'
        ? t('update.chipRestarting', { version })
        : pct !== null
          ? t('update.chipDownloadingPct', { version, pct })
          : t('update.chipDownloadingBusy', { version })

  return (
    <button
      type="button"
      onClick={onOpen}
      data-testid="update-chip"
      className={`mx-3 mb-2 rounded-lg border px-2.5 py-2 text-left text-xs transition-colors ${
        status.state === 'ready'
          ? 'border-indigo-200 bg-indigo-50 font-medium text-indigo-700 hover:bg-indigo-100'
          : 'border-gray-200 bg-white text-gray-600 hover:bg-gray-50'
      }`}
    >
      <span>{label}</span>
      {status.state !== 'ready' && (
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
