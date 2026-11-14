import { useTranslation } from 'react-i18next'
import type { ReleaseNotesRange } from '../types'
import { ReleaseNotesSections } from './ReleaseNotesSections'
import { ModalShell } from './ui'

/**
 * "已更新到 v{{version}}": what just arrived, shown once per upgrade.
 *
 * A different surface from UpdateAvailableModal because nothing is
 * available any more. `/update-check` compares against the version
 * ANSWERING, so after the update it says "up to date" and cannot list
 * the releases this machine just crossed — hence the range request
 * (`/api/system/release-notes?since=&until=`) that the flow hook did
 * before rendering this.
 */
export function UpdateAppliedModal({
  range,
  releasesPageUrl,
  onClose,
}: {
  /** The notes for the (from → to) range, from /release-notes. */
  range: ReleaseNotesRange
  releasesPageUrl?: string
  onClose: () => void
}) {
  const { t } = useTranslation()
  return (
    <ModalShell>
      <div className="border-b border-gray-100 p-6 pb-4">
        <h2 className="text-lg font-semibold text-gray-900">
          {t('update.appliedTitle', { version: range.until })}
        </h2>
      </div>
      <div className="overflow-y-auto p-6 pt-4">
        <ReleaseNotesSections
          source={{
            current_version: range.since,
            latest_version: range.until,
            releases: range.releases.map((version) => ({ version })),
            releases_page_url: releasesPageUrl,
            highlights: range.highlights,
            notes: range.notes,
          }}
        />
      </div>
      <div className="flex justify-end border-t border-gray-100 p-4">
        <button
          onClick={onClose}
          className="rounded-lg border border-gray-300 px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-50"
        >
          {t('update.gotIt')}
        </button>
      </div>
    </ModalShell>
  )
}
