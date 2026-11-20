import { useTranslation } from 'react-i18next'
import { useScheduleAttention } from '../hooks/useScheduleAttention'
import type { PlanApprovalItem, Schedule } from '../types'

interface Props {
  /** Reloads of the schedule list re-check what waits for approval. */
  schedules: Schedule[]
  /** Open the given schedule's detail view. */
  onOpen: (item: PlanApprovalItem) => void
}

/**
 * Full-width bar at the very top of the app: scheduled tasks whose
 * plan waits for the user's approval. Nothing runs on those schedules
 * until someone approves, so this sits above every view. Clicking it
 * opens the oldest one. Hidden when nothing waits.
 */
export function PlanApprovalBanner({ schedules, onOpen }: Props) {
  const { t } = useTranslation()
  const approvals = useScheduleAttention(schedules)
  if (approvals.length === 0) return null
  return (
    <button
      type="button"
      onClick={() => onOpen(approvals[0])}
      className="w-full shrink-0 px-4 py-2 bg-amber-400 hover:bg-amber-500 text-amber-950 text-sm font-semibold flex items-center justify-center gap-2 text-center"
      data-testid="plan-approval-banner"
    >
      <span>{t('planApprovalBanner.message', { count: approvals.length })}</span>
      <span aria-hidden="true">→</span>
      <span className="underline underline-offset-2">
        {t('planApprovalBanner.action')}
      </span>
    </button>
  )
}
