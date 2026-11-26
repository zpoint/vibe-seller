/**
 * The wording for an apply that did NOT work, shared by the sidebar chip
 * and the dialog because it is the same sentence in both.
 *
 * Pure (no React, no state) so the one place that decides "this apply
 * needs telling about" cannot drift between the two surfaces — a
 * rollback the chip reports and the dialog calls nothing is exactly the
 * "it looked like it succeeded" bug this is here to avoid.
 */
import type { TFunction } from 'i18next'
import type {
  DownloadFailure,
  LastUpdate,
  ManualRoute,
  UpdateCheckResult,
  UpdateStatus,
} from '../types'

/** The apply to report, or null when the last one ended as it should. */
export function applyFailure(
  last: LastUpdate | null | undefined
): LastUpdate | null {
  return last && last.status !== 'ok' ? last : null
}

/**
 * `rolled_back` is not a plain failure: the machine came back on the
 * version it left, so the line names THAT version — the one the admin
 * is still running.
 */
export function applyFailureLabel(t: TFunction, last: LastUpdate): string {
  return last.status === 'rolled_back'
    ? t('update.rolledBack', { version: last.from })
    : t('update.applyFailed', { version: last.to })
}

/**
 * The failed download the manual guide is about, or null.
 *
 * Only while the server says the guide is due (`manual_due`: a failure
 * stands and no newer attempt is under way). While a retry downloads,
 * the chip shows its progress instead; the server decides, not the page.
 */
export function downloadFailure(
  status: UpdateStatus | null | undefined
): DownloadFailure | null {
  if (!status?.supported || !status.manual_due) return null
  return status.download_failure ?? null
}

/**
 * What "稍后" dismisses: THIS failure, not the version. Every new
 * failed attempt is a new key, so the guide comes back after each one
 * until a download succeeds or the admin updates by hand.
 */
export function downloadFailureKey(failure: DownloadFailure): string {
  return `download-failed:${failure.version}:${failure.attempts}`
}

/**
 * How to update by hand. `/update-check`'s answer when it has one; else
 * the server's own (`update-status.manual_update`) — on the very network
 * that made the download fail, the check can come back empty too, and
 * the guide must still say what to do.
 */
export function manualRoute(
  check: UpdateCheckResult | null | undefined,
  status: UpdateStatus | null | undefined
): ManualRoute {
  if (check?.upgrade_command || check?.download_url) {
    return {
      upgrade_command: check.upgrade_command ?? null,
      download_url: check.download_url ?? null,
    }
  }
  return (
    status?.manual_update ?? { upgrade_command: null, download_url: null }
  )
}
