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
import type { LastUpdate } from '../types'

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
