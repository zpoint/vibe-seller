import { useTranslation } from 'react-i18next'
import { useScheduleAttention } from '../hooks/useScheduleAttention'
import { useTaskAttention } from '../hooks/useTaskAttention'
import type { PlanApprovalItem, PlannerQuestionItem, Schedule, WaitingTaskItem } from '../types'

interface Props {
  /** Reloads of the schedule list re-check what waits for the user. */
  schedules: Schedule[]
  /** Open a task waiting for the user (a reply, or its plan to review). */
  onOpenTask: (item: WaitingTaskItem) => void
  /** Open the planning task that asked a question. */
  onOpenQuestion: (item: PlannerQuestionItem) => void
  /** Open the given schedule's detail view (its plan to approve). */
  onOpenApproval: (item: PlanApprovalItem) => void
}

const BAR =
  'w-full shrink-0 px-4 py-2 bg-amber-400 hover:bg-amber-500 text-amber-950 text-sm font-semibold flex items-center justify-center gap-2 text-center'

/**
 * ONE full-width bar at the very top of the app for what waits on the
 * user — never a stack of them. Answering comes before approving: a
 * question holds work that has already started and stops for good at
 * its deadline, while a plan to approve only holds future runs. So the
 * bar shows, in this order, the first kind that has anything:
 *
 *   1. tasks waiting for the user's reply — a question or any other
 *      wait only a reply ends (GET /api/tasks/attention);
 *   2. review schedules whose planner asked a question;
 *   3. plan-mode tasks whose plan waits for review (same endpoint);
 *   4. schedule plans waiting for approval.
 *
 * It opens the oldest item of its kind; once the last one is handled,
 * the next kind takes its place. Hidden when nothing waits.
 */
export function AttentionBanner({ schedules, onOpenTask, onOpenQuestion, onOpenApproval }: Props) {
  const { t } = useTranslation()
  const { approvals, questions } = useScheduleAttention(schedules)
  const tasks = useTaskAttention()
  const waiting = tasks.filter(item => item.kind !== 'plan')
  const plans = tasks.filter(item => item.kind === 'plan')
  if (waiting.length > 0) {
    return (
      <button type="button" onClick={() => onOpenTask(waiting[0])} className={BAR} data-testid="waiting-task-banner">
        <span>{t('waitingTaskBanner.message', { count: waiting.length })}</span>
        <span aria-hidden="true">→</span>
        <span className="underline underline-offset-2">{t('waitingTaskBanner.action')}</span>
      </button>
    )
  }
  if (questions.length > 0) {
    return (
      <button type="button" onClick={() => onOpenQuestion(questions[0])} className={BAR} data-testid="planner-question-banner">
        <span>{t('planApprovalBanner.question', { count: questions.length })}</span>
        <span aria-hidden="true">→</span>
        <span className="underline underline-offset-2">{t('planApprovalBanner.answer')}</span>
      </button>
    )
  }
  if (plans.length > 0) {
    return (
      <button type="button" onClick={() => onOpenTask(plans[0])} className={BAR} data-testid="task-plan-banner">
        <span>{t('waitingTaskBanner.plan', { count: plans.length })}</span>
        <span aria-hidden="true">→</span>
        <span className="underline underline-offset-2">{t('waitingTaskBanner.review')}</span>
      </button>
    )
  }
  if (approvals.length > 0) {
    return (
      <button type="button" onClick={() => onOpenApproval(approvals[0])} className={BAR} data-testid="plan-approval-banner">
        <span>{t('planApprovalBanner.message', { count: approvals.length })}</span>
        <span aria-hidden="true">→</span>
        <span className="underline underline-offset-2">{t('planApprovalBanner.action')}</span>
      </button>
    )
  }
  return null
}
