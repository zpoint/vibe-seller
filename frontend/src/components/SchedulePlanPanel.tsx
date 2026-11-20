import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import Markdown from 'react-markdown'
import { REMARK_PLUGINS } from '../lib/markdownPlugins'

import { api } from '../api'
import type { Schedule, SchedulePlanStatus } from '../types'

/** Planning-task row returned by GET /api/schedules/{id}/plan. */
export interface PlanningTaskSummary {
  id: string
  status: string
  created_at: string
  completed_at: string | null
  error: string | null
}

export interface SchedulePlanResponse {
  plan_status: SchedulePlanStatus
  plan_version: number
  /** The last APPROVED plan (what runs). */
  plan_text: string | null
  /** A plan the AI wrote that waits for approval. */
  proposed_plan?: string | null
  /** The user's last "request changes" text. */
  plan_feedback?: string | null
  plan_error: string | null
  current_planning_task_id: string | null
  planning_task_history: PlanningTaskSummary[]
  finalize_description?: string | null
}

interface Props {
  schedule: Schedule
  /** Select the planning task and open its detail panel. */
  onOpenTask: (taskId: string) => void
  /** Spawn a new planning task (POST /replan). */
  onReplan: (scheduleId: string) => Promise<void> | void
  /** Approve / request changes return the updated schedule. */
  onUpdated?: (schedule: Schedule) => void
}

const STATE_ACCENT: Record<SchedulePlanStatus, string> = {
  pending_approval: 'border-l-amber-500 bg-amber-50/60',
  ready: 'border-l-green-500 bg-green-50/40',
  planning: 'border-l-indigo-500 bg-indigo-50/40',
  stale: 'border-l-orange-500 bg-orange-50/40',
  failed: 'border-l-red-500 bg-red-50/40',
  none: 'border-l-amber-500 bg-amber-50/40',
}

const STATUS_PILL: Record<SchedulePlanStatus, string> = {
  pending_approval: 'bg-amber-200 text-amber-900',
  ready: 'bg-green-100 text-green-800',
  planning: 'bg-indigo-100 text-indigo-800',
  stale: 'bg-orange-100 text-orange-800',
  failed: 'bg-red-100 text-red-800',
  none: 'bg-amber-100 text-amber-800',
}

const PROSE =
  'prose prose-sm max-w-none prose-p:my-1.5 prose-ul:my-1.5 prose-li:my-0.5'

// A revision in flight: the approved plan keeps running meanwhile.
const REVISION_STATES: SchedulePlanStatus[] = [
  'planning',
  'pending_approval',
  'failed',
]

export function SchedulePlanPanel({
  schedule,
  onOpenTask,
  onReplan,
  onUpdated,
}: Props) {
  const { t, i18n } = useTranslation()
  const [plan, setPlan] = useState<SchedulePlanResponse | null>(null)
  const [loading, setLoading] = useState(true)
  // Plan is collapsed by default — it can be long. User clicks to expand.
  const [planExpanded, setPlanExpanded] = useState(false)
  const [historyExpanded, setHistoryExpanded] = useState(false)
  const [replanning, setReplanning] = useState(false)
  const [feedbackOpen, setFeedbackOpen] = useState(false)
  const [feedback, setFeedback] = useState('')
  const [acting, setActing] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    try {
      const r = await api.get(`/api/schedules/${schedule.id}/plan`)
      setPlan(r as SchedulePlanResponse)
    } catch {
      /* non-fatal — banner drives off schedule.plan_status fallback */
    } finally {
      setLoading(false)
    }
  }, [schedule.id])

  // Reset schedule-scoped UI state + reload when the selected
  // schedule changes. Without this, expanding the plan on schedule A
  // and then selecting schedule B would open B with the plan
  // already expanded (leaked UI state).
  useEffect(() => {
    setLoading(true)
    setPlan(null)
    setPlanExpanded(false)
    setHistoryExpanded(false)
    setActionError(null)
    refresh()
  }, [schedule.id, schedule.plan_status, schedule.plan_version, refresh])
  // The feedback draft belongs to one schedule only.
  useEffect(() => {
    setFeedbackOpen(false)
    setFeedback('')
  }, [schedule.id])

  const handleReplan = useCallback(async () => {
    if (replanning) return
    setReplanning(true)
    try {
      await onReplan(schedule.id)
      await refresh()
    } finally {
      setReplanning(false)
    }
  }, [onReplan, schedule.id, refresh, replanning])

  // Approve / request changes carry the plan_version the user read, so
  // the server refuses (412) when the plan changed underneath them.
  const act = useCallback(
    async (path: 'approve' | 'request-changes', body: object) => {
      if (acting) return false
      setActing(true)
      setActionError(null)
      try {
        const version = plan?.plan_version ?? schedule.plan_version
        const updated = (await api.post(
          `/api/schedules/${schedule.id}/plan/${path}`,
          { ...body, plan_version: version },
        )) as Schedule
        onUpdated?.(updated)
        await refresh()
        return true
      } catch (e) {
        setActionError(e instanceof Error ? e.message : String(e))
        await refresh()
        return false
      } finally {
        setActing(false)
      }
    },
    [acting, plan, schedule.id, schedule.plan_version, onUpdated, refresh],
  )
  const handleApprove = useCallback(() => act('approve', {}), [act])
  const handleSendFeedback = useCallback(async () => {
    const text = feedback.trim()
    if (!text) return
    if (await act('request-changes', { feedback: text })) {
      setFeedback('')
      setFeedbackOpen(false)
    }
  }, [act, feedback])

  // System schedules bypass the plan lifecycle — render nothing.
  if (schedule.is_system || !schedule.plan_mode) return null

  // Prefer the `/plan` response, fall back to the schedule fields on
  // first render while we're still loading (avoids a flash of empty).
  const status: SchedulePlanStatus = plan?.plan_status ?? schedule.plan_status
  const planText = plan?.plan_text ?? schedule.plan
  const proposedText = plan
    ? plan.proposed_plan ?? null
    : schedule.proposed_plan ?? null
  const revising = !!planText && REVISION_STATES.includes(status)
  const planVersion = plan?.plan_version ?? schedule.plan_version
  const planError = plan?.plan_error ?? schedule.plan_error
  const planningTaskId =
    plan?.current_planning_task_id ?? schedule.current_planning_task_id
  const history = plan?.planning_task_history ?? []
  const finalizeDescription =
    plan?.finalize_description ?? schedule.finalize_description ?? null

  const cta = renderCta({
    status,
    replanning,
    onReplan: handleReplan,
    planningTaskId,
    onOpenTask,
    acting,
    onApprove: handleApprove,
    onRequestChanges: () => setFeedbackOpen(v => !v),
    t,
  })

  return (
    <div
      className={`mb-4 rounded-lg border border-l-4 border-gray-200 ${STATE_ACCENT[status]}`}
      data-testid="schedule-plan-panel"
    >
      <div className="px-4 py-3 flex items-center gap-2 flex-wrap">
        <span className="text-sm font-semibold text-gray-700">
          {t('schedules.plan.heading')}
        </span>
        <span
          className={`px-2 py-0.5 text-xs rounded-full font-medium ${STATUS_PILL[status]}`}
          data-testid="schedule-plan-status"
        >
          {status === 'pending_approval'
            ? t('schedules.plan.waitingPill')
            : t(`schedules.planStatus.${status}`)}
        </span>
        {status === 'ready' && (
          <span className="text-xs text-gray-500">
            {t('schedules.plan.planVersion', { n: planVersion })}
          </span>
        )}
        <div className="flex-1" />
        {cta}
      </div>

      <div className="px-4 pb-3 text-xs text-gray-600">
        {t(`schedules.plan.explainer.${status}`)}
      </div>

      {revising && (
        <div
          className="px-4 pb-3 text-xs font-medium text-gray-700"
          data-testid="plan-approved-keeps-running"
        >
          {t('schedules.plan.approvedKeepsRunning')}
        </div>
      )}

      {feedbackOpen && (
        <div className="px-4 pb-3 space-y-2" data-testid="plan-feedback">
          <textarea
            value={feedback}
            onChange={e => setFeedback(e.target.value)}
            rows={3}
            placeholder={t('schedules.plan.feedbackPlaceholder')}
            className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm bg-white resize-y focus:outline-none focus:ring-2 focus:ring-amber-500"
            data-testid="plan-feedback-input"
            autoFocus
          />
          <div className="flex justify-end gap-2">
            <button
              onClick={() => setFeedbackOpen(false)}
              className="px-3 py-1.5 text-xs rounded font-medium bg-gray-100 text-gray-700 hover:bg-gray-200"
            >
              {t('common.cancel')}
            </button>
            <button
              onClick={handleSendFeedback}
              disabled={acting || !feedback.trim()}
              className="px-3 py-1.5 text-xs rounded font-medium bg-amber-600 text-white hover:bg-amber-700 disabled:opacity-50"
              data-testid="plan-feedback-send"
            >
              {t('schedules.plan.send')}
            </button>
          </div>
        </div>
      )}

      {actionError && (
        <div
          className="mx-4 mb-3 px-3 py-2 text-xs bg-red-50 border border-red-100 text-red-800 rounded break-words"
          role="alert"
          data-testid="plan-action-error"
        >
          {actionError}
        </div>
      )}

      {planError && (
        <div className="mx-4 mb-3 px-3 py-2 text-xs font-mono bg-red-50 border border-red-100 text-red-800 rounded break-all">
          {planError}
        </div>
      )}

      {/* The proposal awaiting approval — open by default: reading it
      is the whole point of this state. */}
      {status === 'pending_approval' && proposedText && (
        <div className="border-t border-amber-200 bg-white/70">
          <div className="px-4 pt-3 text-xs font-semibold text-amber-900">
            {t('schedules.plan.proposedHeading')}
          </div>
          <div
            className={`px-4 pb-4 text-sm text-gray-700 ${PROSE}`}
            data-testid="plan-proposed-markdown"
          >
            <Markdown remarkPlugins={REMARK_PLUGINS}>{proposedText}</Markdown>
          </div>
        </div>
      )}

      {/* The approved plan (or the stale prior one), collapsed by
      default — it can be long. User clicks to expand. */}
      {planText && (status === 'ready' || status === 'stale' || revising) && (
        <div className="border-t border-gray-100">
          <button
            onClick={() => setPlanExpanded(v => !v)}
            className="w-full px-4 py-2 text-xs text-gray-600 hover:bg-gray-50 flex items-center gap-2 group"
            data-testid="plan-toggle"
            aria-expanded={planExpanded}
          >
            <span
              className={`inline-block transition-transform text-gray-400 group-hover:text-gray-600 ${planExpanded ? 'rotate-90' : ''}`}
              aria-hidden="true"
            >
              ▶
            </span>
            <span className="font-medium">
              {planExpanded
                ? t('schedules.plan.collapse')
                : t('schedules.plan.expand')}
            </span>
            {revising && (
              <span className="text-[10px] text-gray-500">
                {t('schedules.plan.approvedHeading')}
              </span>
            )}
            {status === 'stale' && (
              <span className="text-[10px] text-orange-700">
                {t('schedules.planStatus.stale')}
              </span>
            )}
          </button>
          {planExpanded && (
            <div
              className={`px-4 pb-4 text-sm text-gray-700 ${PROSE}`}
              data-testid="plan-markdown"
            >
              <Markdown remarkPlugins={REMARK_PLUGINS}>{planText}</Markdown>
            </div>
          )}
        </div>
      )}

      {schedule.fanout_serial && (
        <div
          className="border-t border-gray-100 px-4 py-3 text-xs text-gray-500"
          data-testid="schedule-fanout-serial"
        >
          {t('schedules.plan.fanoutSerial')}
        </div>
      )}

      {/* Finalize step the plan agent registered — gives the user
      visibility that, after all per-store children finish, one extra
      combine/reduce step runs (e.g. a single PR). No action needed. */}
      {finalizeDescription && (
        <div
          className="border-t border-gray-100 px-4 py-3"
          data-testid="schedule-finalize-step"
        >
          <div className="flex items-center gap-1.5 text-xs font-semibold text-green-800">
            <span aria-hidden="true">✅</span>
            {t('schedules.plan.finalize.heading')}
          </div>
          <div className="mt-1 text-xs text-gray-500">
            {t('schedules.plan.finalize.explainer')}
          </div>
          <div
            className={`mt-2 text-sm text-gray-700 ${PROSE}`}
            data-testid="schedule-finalize-text"
          >
            <Markdown remarkPlugins={REMARK_PLUGINS}>
              {finalizeDescription}
            </Markdown>
          </div>
        </div>
      )}

      {/* History disclosure — always clickable. An empty history
      state is still informative ("No prior planning attempts."),
      and gating `disabled` on a fetch-driven value races tests
      that click the toggle before the /plan response lands. */}
      <div className="border-t border-gray-100">
        <button
          onClick={() => setHistoryExpanded(v => !v)}
          className="w-full px-4 py-2 text-xs text-gray-600 hover:bg-gray-50 flex items-center gap-2 group"
          data-testid="plan-history-toggle"
          aria-expanded={historyExpanded}
        >
          <span
            className={`inline-block transition-transform text-gray-400 group-hover:text-gray-600 ${historyExpanded ? 'rotate-90' : ''} ${history.length === 0 ? 'opacity-40' : ''}`}
            aria-hidden="true"
          >
            ▶
          </span>
          <span className="font-medium">
            {t('schedules.plan.history.heading', { count: history.length })}
          </span>
        </button>
        {historyExpanded && (
          <ul
            className="px-4 pb-3 space-y-1 text-xs text-gray-600"
            data-testid="plan-history-list"
          >
            {history.length === 0 ? (
              <li className="text-gray-400">
                {t('schedules.plan.history.empty')}
              </li>
            ) : (
              history.map((row, idx) => (
                <li key={row.id} className="flex items-center gap-2">
                  <button
                    onClick={() => onOpenTask(row.id)}
                    className="text-indigo-600 hover:text-indigo-800 hover:underline"
                  >
                    {t('schedules.plan.history.attempt', {
                      n: history.length - idx,
                      status: row.status,
                      when: formatDate(row.created_at, i18n.language),
                    })}
                  </button>
                  {row.error && (
                    <span className="text-red-600 truncate" title={row.error}>
                      — {row.error.slice(0, 80)}
                    </span>
                  )}
                </li>
              ))
            )}
          </ul>
        )}
      </div>

      {loading && (
        <div
          className="px-4 py-1 text-[10px] text-gray-400"
          data-testid="plan-loading"
        >
          …
        </div>
      )}
    </div>
  )
}

function renderCta({
  status,
  replanning,
  onReplan,
  planningTaskId,
  onOpenTask,
  acting,
  onApprove,
  onRequestChanges,
  t,
}: {
  status: SchedulePlanStatus
  replanning: boolean
  onReplan: () => void
  planningTaskId: string | null
  onOpenTask: (id: string) => void
  acting: boolean
  onApprove: () => void
  onRequestChanges: () => void
  t: (k: string, opts?: Record<string, unknown>) => string
}) {
  const requestChanges = (
    <button
      onClick={onRequestChanges}
      disabled={acting}
      className="px-3 py-1.5 text-xs rounded font-medium bg-white border border-amber-300 text-amber-800 hover:bg-amber-50 disabled:opacity-50"
      data-testid="plan-request-changes"
    >
      {t('schedules.plan.requestChanges')}
    </button>
  )
  if (status === 'pending_approval') {
    return (
      <div className="flex gap-2">
        {requestChanges}
        <button
          onClick={onApprove}
          disabled={acting}
          className="px-3 py-1.5 text-xs rounded font-semibold bg-green-600 text-white hover:bg-green-700 disabled:opacity-50"
          data-testid="plan-approve"
        >
          {t('schedules.plan.approve')}
        </button>
      </div>
    )
  }
  if (status === 'planning' && planningTaskId) {
    return (
      <button
        onClick={() => onOpenTask(planningTaskId)}
        className="px-3 py-1.5 text-xs rounded font-medium bg-indigo-600 text-white hover:bg-indigo-700"
        data-testid="plan-open-planning-task"
      >
        {t('schedules.plan.openPlanningTask')}
      </button>
    )
  }
  if (status === 'none') {
    return (
      <button
        onClick={onReplan}
        disabled={replanning}
        className="px-3 py-1.5 text-xs rounded font-medium bg-amber-600 text-white hover:bg-amber-700 disabled:opacity-50"
        data-testid="plan-start"
      >
        {t('schedules.plan.startPlanning')}
      </button>
    )
  }
  if (status === 'stale' || status === 'failed') {
    return (
      <button
        onClick={onReplan}
        disabled={replanning}
        className="px-3 py-1.5 text-xs rounded font-medium bg-orange-600 text-white hover:bg-orange-700 disabled:opacity-50"
        data-testid="plan-replan"
      >
        {status === 'failed'
          ? t('schedules.plan.tryAgain')
          : t('schedules.plan.replan')}
      </button>
    )
  }
  if (status === 'ready') {
    return (
      <div className="flex gap-2">
        {requestChanges}
        <button
          onClick={onReplan}
          disabled={replanning}
          className="px-3 py-1.5 text-xs rounded font-medium bg-gray-100 text-gray-700 hover:bg-gray-200 disabled:opacity-50"
          data-testid="plan-replan"
        >
          {t('schedules.plan.replan')}
        </button>
      </div>
    )
  }
  return null
}

function formatDate(iso: string, locale: string): string {
  try {
    return new Date(iso).toLocaleString(locale)
  } catch {
    return iso
  }
}
