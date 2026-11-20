import { useTranslation } from 'react-i18next'
import { useScheduleAttention } from '../hooks/useScheduleAttention'
import type { PlanApprovalItem, PlannerQuestionItem, Schedule } from '../types'

interface Props {
  /** Reloads of the schedule list re-check what waits for the user. */
  schedules: Schedule[]
  /** Open the given schedule's detail view. */
  onOpen: (item: PlanApprovalItem) => void
  /** Open the planning task that asked a question. */
  onOpenQuestion: (item: PlannerQuestionItem) => void
}

const BAR =
  'w-full shrink-0 px-4 py-2 bg-amber-400 hover:bg-amber-500 text-amber-950 text-sm font-semibold flex items-center justify-center gap-2 text-center'

/**
 * Full-width bars at the very top of the app for what waits on the user
 * in scheduled tasks: plans to approve (nothing runs until someone
 * does), and plans the AI cannot finish until a question is answered.
 * Each opens the oldest item. Hidden when nothing waits.
 */
export function PlanApprovalBanner({ schedules, onOpen, onOpenQuestion }: Props) {
  const { t } = useTranslation()
  const { approvals, questions } = useScheduleAttention(schedules)
  if (approvals.length === 0 && questions.length === 0) return null
  return (
    <>
      {approvals.length > 0 && (
        <button type="button" onClick={() => onOpen(approvals[0])} className={BAR} data-testid="plan-approval-banner">
          <span>{t('planApprovalBanner.message', { count: approvals.length })}</span>
          <span aria-hidden="true">→</span>
          <span className="underline underline-offset-2">{t('planApprovalBanner.action')}</span>
        </button>
      )}
      {questions.length > 0 && (
        <button type="button" onClick={() => onOpenQuestion(questions[0])} className={BAR} data-testid="planner-question-banner">
          <span>{t('planApprovalBanner.question', { count: questions.length })}</span>
          <span aria-hidden="true">→</span>
          <span className="underline underline-offset-2">{t('planApprovalBanner.answer')}</span>
        </button>
      )}
    </>
  )
}
