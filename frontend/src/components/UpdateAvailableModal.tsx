import { useTranslation } from 'react-i18next'
import type { UpdateCheckResult, UpdateStatus } from '../types'
import { ReleaseNotesSections } from './ReleaseNotesSections'

type Props = {
  check: UpdateCheckResult
  status: UpdateStatus | null
  isAdmin: boolean
  onClose: () => void
  onRestart: () => void | Promise<void>
  restartError: string | null
  restartTimedOut: boolean
}

const MB = 1024 * 1024

/**
 * The update modal, in two experiences on one surface:
 *
 * - **In-app (supported installs)**: mirrors the download state
 *   machine — progress while the release downloads, the
 *   "重启并更新" action once it is staged, and a waiting overlay
 *   after the restart is requested (the page reloads itself when the
 *   server answers on the new version).
 * - **Manual (pip / docker / source / switched-off)**: today's popup —
 *   the copy-able upgrade command (macOS/Linux) or the installer
 *   download link (Windows).
 *
 * Both show the merged release notes (every skipped version's
 * Highlights/Notes by section). The app's real Tailwind styling wins
 * over the mockup's CSS; the Chinese texts are the approved ones.
 */
export function UpdateAvailableModal({
  check,
  status,
  isAdmin,
  onClose,
  onRestart,
  restartError,
  restartTimedOut,
}: Props) {
  const { t } = useTranslation()
  const supported = status?.supported === true
  const state = status?.state ?? 'idle'
  const isWindows = status?.platform === 'windows'
  const target = status?.version ?? check.latest_version ?? ''
  const current = check.current_version ?? status?.current_version ?? ''

  const nightly = check.channel === 'nightly' && (
    <span className="ml-2 align-middle rounded bg-amber-100 px-1.5 py-0.5 text-xs font-medium text-amber-800">
      {t('updateCheck.nightlyBadge')}
    </span>
  )

  // The in-app flow's header and notes follow the version the button
  // will actually apply (the staged installer / prefetched wheels),
  // not update-check's PyPI latest — they can differ during a release
  // window on Windows.
  const notes = (
    <div className="overflow-y-auto p-6 pt-4">
      <ReleaseNotesSections
        check={check}
        targetVersion={supported ? target : null}
      />
    </div>
  )

  if (supported && state === 'restarting') {
    return (
      <Shell>
        <div className="flex flex-col items-center gap-4 p-8 text-center">
          <div
            className="h-8 w-8 animate-spin rounded-full border-[3px] border-indigo-100 border-t-indigo-600"
            aria-hidden
          />
          <h2 className="text-lg font-semibold text-gray-900">
            {t('update.restartingTitle', { version: target })}
          </h2>
          <p className="max-w-md text-sm leading-relaxed text-gray-600">
            {isWindows
              ? t('update.restartingWindows')
              : t('update.restartingUnix')}
          </p>
          {restartTimedOut && (
            <div className="w-full rounded-lg border border-amber-200 bg-amber-50 p-4 text-left text-sm text-amber-800">
              <p className="mb-2 font-medium">
                {t('update.restartTimedOut', { version: target })}
              </p>
              <ManualInstructions check={check} />
            </div>
          )}
        </div>
      </Shell>
    )
  }

  if (supported && state === 'downloading') {
    const pct =
      status?.total_bytes && status.downloaded_bytes !== null
        ? Math.min(100, Math.floor((status.downloaded_bytes / status.total_bytes) * 100))
        : null
    return (
      <Shell>
        <div className="border-b border-gray-100 p-6 pb-4">
          <h2 className="text-lg font-semibold text-gray-900">
            {t('update.downloadingTitle', { version: target })}
            {nightly}
          </h2>
          <p className="mt-1 text-sm text-gray-600">
            {t('update.downloadingSub', { current })}
          </p>
          <div className="mt-4 rounded-lg bg-gray-50 p-3">
            <div className="flex items-center justify-between text-xs text-gray-500">
              <span>
                {isWindows
                  ? t('update.downloadingPackage')
                  : t('update.downloadingNewVersion')}
              </span>
              {pct !== null && (
                <span className="font-mono tabular-nums">
                  {Math.floor((status?.downloaded_bytes ?? 0) / MB)} /{' '}
                  {Math.round(status!.total_bytes! / MB)} MB · {pct}%
                </span>
              )}
            </div>
            <div className="mt-2 h-1 w-full overflow-hidden rounded-full bg-gray-200">
              {pct !== null ? (
                <div
                  className="h-full rounded-full bg-indigo-600"
                  style={{ width: `${pct}%` }}
                />
              ) : (
                <div className="h-full w-1/3 animate-pulse rounded-full bg-indigo-400" />
              )}
            </div>
          </div>
        </div>
        {notes}
        <div className="flex justify-end border-t border-gray-100 p-4">
          <button
            onClick={onClose}
            className="rounded-lg border border-gray-300 px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-50"
          >
            {t('update.close')}
          </button>
        </div>
      </Shell>
    )
  }

  if (supported && state === 'ready') {
    const tasks = status?.running_tasks ?? 0
    const subtitle = !isAdmin
      ? t('update.readySubMember', { current, version: target })
      : isWindows
        ? t('update.readySubAdminWindows', { current })
        : t('update.readySubAdminUnix', { current })
    return (
      <Shell>
        <div className="border-b border-gray-100 p-6 pb-4">
          <h2 className="text-lg font-semibold text-gray-900">
            {t('update.readyTitle', { version: target })}
            {nightly}
          </h2>
          <p className="mt-1 text-sm leading-relaxed text-gray-600">{subtitle}</p>
          {isAdmin && isWindows && (
            <div className="mt-3 rounded-lg bg-gray-100 px-3 py-2 text-sm text-gray-600">
              {t('update.windowsMachineNote')}
            </div>
          )}
          {isAdmin && tasks > 0 && (
            <div className="mt-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm leading-relaxed text-amber-800">
              <span className="font-semibold">
                {t('update.runningTasksLead', { count: tasks })}
              </span>
              {t('update.runningTasksTail')}
            </div>
          )}
          {restartError && (
            <div className="mt-3 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
              {restartError}
            </div>
          )}
        </div>
        {notes}
        <div className="flex justify-end gap-2 border-t border-gray-100 p-4">
          {!isAdmin ? (
            <button
              onClick={onClose}
              className="rounded-lg border border-gray-300 px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-50"
            >
              {t('update.gotIt')}
            </button>
          ) : (
            <>
              <button
                onClick={onClose}
                className="rounded-lg border border-gray-300 px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-50"
              >
                {t('update.later')}
              </button>
              <button
                onClick={() => void onRestart()}
                className="rounded-lg bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-700"
              >
                {tasks > 0 ? t('update.restartAnyway') : t('update.restart')}
              </button>
            </>
          )}
        </div>
      </Shell>
    )
  }

  // ── manual experience (unsupported install, or a failed stage) ──
  return (
    <Shell>
      <div className="border-b border-gray-100 p-6 pb-4">
        <h2 className="text-lg font-semibold text-gray-900">
          {t('updateCheck.title')}
          {nightly}
        </h2>
        <p className="mt-1 text-sm text-gray-600">
          {t('updateCheck.versionLine', {
            current: check.current_version,
            latest: check.latest_version,
          })}
        </p>
        {supported && state === 'error' && status?.error && (
          <div className="mt-3 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
            <span>{t('update.autoUpdateFailed')}</span>:{' '}
            <span>{status.error}</span>
          </div>
        )}
        <ManualInstructions check={check} />
      </div>
      {notes}
      <div className="flex justify-end border-t border-gray-100 p-4">
        <button
          onClick={onClose}
          className="rounded-lg border border-gray-300 px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-50"
        >
          {t('updateCheck.remindLater')}
        </button>
      </div>
    </Shell>
  )
}

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <div
      role="dialog"
      aria-modal="true"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
    >
      <div className="flex max-h-[85vh] w-full max-w-lg flex-col rounded-lg bg-white shadow-xl">
        {children}
      </div>
    </div>
  )
}

function ManualInstructions({ check }: { check: UpdateCheckResult }) {
  const { t } = useTranslation()
  const copy = async () => {
    if (!check.upgrade_command) return
    try {
      await navigator.clipboard.writeText(check.upgrade_command)
    } catch {
      /* clipboard blocked — user can still select+copy manually */
    }
  }
  if (check.upgrade_command) {
    return (
      <div className="mt-4">
        <p className="text-sm text-gray-700">{t('updateCheck.macLinuxInstructions')}</p>
        <div className="mt-2 flex items-center gap-2">
          <pre className="flex-1 overflow-auto rounded bg-gray-100 p-2.5 font-mono text-xs text-gray-900">
            {check.upgrade_command}
          </pre>
          <button
            onClick={() => void copy()}
            className="shrink-0 rounded border border-gray-300 px-3 py-1.5 text-xs font-medium text-gray-700 hover:bg-gray-50"
          >
            {t('updateCheck.copyCommand')}
          </button>
        </div>
      </div>
    )
  }
  if (check.download_url) {
    return (
      <div className="mt-4">
        <p className="text-sm text-gray-700">{t('updateCheck.windowsInstructions')}</p>
        <a
          href={check.download_url}
          target="_blank"
          rel="noopener noreferrer"
          className="mt-2 inline-block rounded bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-700"
        >
          {t('updateCheck.downloadButton')}
        </a>
      </div>
    )
  }
  return null
}
