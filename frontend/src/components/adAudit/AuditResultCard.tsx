import { useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { parseReport } from '../../lib/adAudit/parseReport'
import type { AdDeclaration } from '../../lib/adAudit/declaration'
import { droppedCampaigns, narrowToScope } from '../../lib/adAudit/declaration'
import type { DecisionSubmission } from '../../lib/adAudit/types'
import { auditHeadline } from '../../lib/adAudit/review'
import { AuditConsole } from './AuditConsole'

interface Props {
  /** The report markdown, as resolved by the server. */
  report: string
  /**
   * What this phase declared it was about. The console renders the
   * report reduced to that scope: the obligation being scoped is what
   * stops out-of-scope rows being PRODUCED, and this is what stops them
   * being ACTED ON when they turn up anyway.
   */
  declaration?: AdDeclaration | null
  /**
   * Whether the console is open, and how to open/close it. Driven by the
   * URL rather than local state so the review is bookmarkable, survives a
   * reload, and can be sent to whoever owns the budget.
   */
  open: boolean
  onOpen: () => void
  onClose: () => void
  onSubmit?: (submission: DecisionSubmission) => void
  submitting?: boolean
}

/**
 * What an ad audit shows in the conversation instead of its raw markdown.
 *
 * A 190 KB report rendered as markdown is unreviewable — a thousand rows of
 * tables with no way to act on any of them. So the result becomes a summary
 * with one way in, and the decisions get made in the console.
 */
export function AuditResultCard({
  report,
  declaration,
  open,
  onOpen,
  onClose,
  onSubmit,
  submitting,
}: Props) {
  const { t } = useTranslation()
  const full = useMemo(() => parseReport(report), [report])
  const doc = useMemo(
    () => narrowToScope(full, declaration ?? null),
    [full, declaration],
  )
  // Campaigns the report carried but this phase never claimed. Shown as
  // a count rather than hidden silently: a reviewer who asked about one
  // SKU should be able to tell the page filtered, not that the audit
  // came back thin.
  const dropped = useMemo(() => droppedCampaigns(full, doc), [full, doc])
  const head = useMemo(() => auditHeadline(doc), [doc])

  return (
    <>
      <div className="bg-white rounded-2xl border border-gray-200 shadow-sm p-5 sm:p-6">
        <div className="flex items-center gap-2 mb-3">
          <span className="w-1.5 h-1.5 rounded-full bg-indigo-500" />
          <span className="text-[11px] font-semibold uppercase tracking-[0.08em] text-indigo-600">
            {t('audit.cardEyebrow')}
          </span>
        </div>
        <p className="text-gray-900 text-base font-semibold mb-1">
          {t('audit.heroTitle', {
              markets: t('audit.marketCount', { count: head.markets }),
              campaigns: t('audit.campaignCount', {
                count: head.campaigns,
              }),
            })}
        </p>
        <p className="text-sm text-gray-600 mb-4">
          {t('audit.cardSummary', {
            rows: head.rows,
            acting: head.acting,
          })}
          {head.quarantined > 0 && (
            <>
              {' · '}
              <span className="text-red-700 font-medium">
                {t('audit.tile.untrusted', { count: head.quarantined })}
              </span>
            </>
          )}
          {dropped > 0 && (
            <>
              {' · '}
              <span className="text-gray-500">
                {t('audit.outOfScopeHidden', { count: dropped })}
              </span>
            </>
          )}
        </p>
        <button
          type="button"
          onClick={onOpen}
          className="px-4 py-2 rounded-lg bg-indigo-600 text-white text-sm font-semibold hover:bg-indigo-700"
        >
          {t('audit.openConsole')}
        </button>
      </div>

      {open && (
        <div
          className="fixed inset-0 z-50 bg-black/40 flex items-stretch justify-center p-0 sm:p-6"
          role="dialog"
          aria-modal="true"
          aria-label={t('audit.cardEyebrow')}
        >
          <div className="bg-white w-full max-w-[1700px] rounded-none sm:rounded-2xl overflow-hidden flex flex-col shadow-2xl">
            <div className="flex items-center gap-3 px-5 py-3 border-b border-gray-200 shrink-0">
              <span className="text-sm font-semibold text-gray-900">
                {t('audit.cardEyebrow')}
              </span>
              <span className="flex-1" />
              <button
                type="button"
                onClick={onClose}
                className="px-3 py-1.5 rounded-lg border border-gray-200 text-sm text-gray-600 hover:bg-gray-50"
              >
                {t('common.close')}
              </button>
            </div>
            <div className="flex-1 overflow-y-auto">
              <AuditConsole
                doc={doc}
                onSubmit={onSubmit}
                submitting={submitting}
              />
            </div>
          </div>
        </div>
      )}
    </>
  )
}
