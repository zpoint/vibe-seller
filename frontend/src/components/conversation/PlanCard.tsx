import { useState, useEffect, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { CollapsibleSection } from '../ui'
import { sendEvent, durationBucket } from '../../lib/telemetry'
import { FrontendEvent } from '../../lib/telemetryEvents'
import type { PlanVersion, TodoItem } from '../../types'

const PROSE_BASE = 'prose prose-sm max-w-none prose-code:text-xs prose-code:font-mono prose-code:text-gray-800 prose-code:bg-gray-100 prose-code:px-1 prose-code:py-0.5 prose-code:rounded prose-code:before:content-none prose-code:after:content-none prose-pre:bg-gray-50 prose-pre:text-gray-800 prose-pre:border prose-pre:border-gray-200 prose-pre:rounded prose-pre:p-2 prose-pre:my-2'
const PROSE_COMPACT = `${PROSE_BASE} prose-p:my-1 prose-ul:my-1 prose-li:my-0.5`

interface PlanCardProps {
  plan: PlanVersion
  allVersions?: PlanVersion[]
  todoItems: TodoItem[]
  taskStatus: string
  planMode: boolean
  scheduleId: string | null
  onConfirm?: () => void
  onRequestChanges?: () => void
}

function parsePlanSections(plan: string) {
  const lines = plan.split('\n')
  const sections: { heading: string; content: string[] }[] = []
  let current: { heading: string; content: string[] } | null = null
  for (const line of lines) {
    const hMatch = line.match(/^#{1,3}\s+(.+)/)
    if (hMatch) {
      if (current) sections.push(current)
      current = { heading: hMatch[1].trim(), content: [] }
    } else if (current) {
      current.content.push(line)
    } else {
      if (!current) current = { heading: '', content: [] }
      current.content.push(line)
    }
  }
  if (current) sections.push(current)
  return sections
}

export function PlanCard({ plan, allVersions, todoItems, taskStatus, planMode, scheduleId, onConfirm, onRequestChanges }: PlanCardProps) {
  const { t } = useTranslation()
  const [viewingVersion, setViewingVersion] = useState(plan.version)
  const planShownAt = useRef(0)
  const hadChangesRequest = useRef(false)
  useEffect(() => {
    planShownAt.current = Date.now()
    hadChangesRequest.current = false
  }, [plan.version])

  // Superseded plans in the stream render as thin markers
  if (!plan.isCurrent) {
    return (
      <div className="py-0.5 text-[10px] text-gray-300 flex items-center gap-1">
        <span>—</span>
        <span>{t('tasks.planSupersededBrief', { version: plan.version })}</span>
      </div>
    )
  }

  const versions = allVersions && allVersions.length > 1 ? allVersions : [plan]
  // Clamp to a valid version — fall back to current if selected version
  // no longer exists (e.g. versions array changed between renders).
  const effectiveVersion = versions.some(v => v.version === viewingVersion) ? viewingVersion : plan.version
  const displayedPlan = versions.find(v => v.version === effectiveVersion) || plan
  const isViewingCurrent = effectiveVersion === plan.version

  const sections = parsePlanSections(displayedPlan.content)
  const collapsibleKeywords = (t('tasks.collapsibleSectionKeywords', { returnObjects: true }) as string[]) || []
  const isCollapsibleSection = (h: string) => collapsibleKeywords.some(kw => h.toLowerCase().includes(kw.toLowerCase()))
  // Approve button is only for ad-hoc plan-mode tasks (no schedule
  // attached). ANY schedule-owned task — plan-only authoring OR a
  // fire — is auto-approved at ExitPlanMode by the gate in
  // `app/task_runner_auto.py`
  // (`auto_approve_plan = bool(task.schedule_id) or not task.plan_mode`),
  // so surfacing the button for plan-only would be misleading:
  // the task has already transitioned COMPLETED by the time the UI
  // paints, and clicking POST /execute-plan would 400.
  const showConfirm =
    isViewingCurrent &&
    taskStatus === 'planned' &&
    planMode &&
    !scheduleId &&
    onConfirm

  return (
    <div className="border-l-4 border-l-indigo-400 bg-white rounded-xl shadow-sm overflow-hidden">
      <div className="px-4 py-2.5 flex items-center gap-2 flex-wrap" data-plan-card>
        {versions.length > 1 ? (
          <div className="flex items-center gap-1">
            {versions.map(v => (
              <button
                key={v.version}
                onClick={() => setViewingVersion(v.version)}
                className={`px-2 py-0.5 text-[10px] font-medium rounded-full transition-colors ${
                  v.version === effectiveVersion
                    ? 'bg-indigo-100 text-indigo-700'
                    : 'bg-gray-100 text-gray-400 hover:bg-gray-200 hover:text-gray-600'
                }`}
              >
                v{v.version}
              </button>
            ))}
          </div>
        ) : (
          <span className="px-2 py-0.5 text-[10px] font-medium rounded-full bg-indigo-100 text-indigo-700">
            {t('tasks.planVersion', { version: plan.version })}
          </span>
        )}
        <span className="text-xs text-gray-400">
          {isViewingCurrent ? t('tasks.currentPlan') : t('tasks.historicalPlan')}
        </span>
        {isViewingCurrent && plan.version > 1 && (
          <span className="text-[10px] text-amber-600 bg-amber-50 px-1.5 py-0.5 rounded-full">
            {t('tasks.revisedFrom', { version: plan.version - 1 })}
          </span>
        )}
      </div>

      {!isViewingCurrent && (
        <div className="px-3 py-1.5 bg-gray-50 border-b border-gray-100 text-xs text-gray-400 flex items-center gap-2">
          <span>{t('tasks.historicalPlan')} v{viewingVersion}</span>
          <button
            onClick={() => setViewingVersion(plan.version)}
            className="text-indigo-500 hover:text-indigo-700 underline"
          >
            {t('tasks.backToCurrent')}
          </button>
        </div>
      )}

      <div className={taskStatus === 'designing' && isViewingCurrent ? 'opacity-50 pointer-events-none' : ''}>
        {sections.map((sec, si) => {
          const contentText = sec.content.join('\n').trim()
          if (!sec.heading && !contentText) return null
          if (sec.heading && contentText && !contentText.includes('\n') && contentText.length < 200) {
            return (
              <div key={si} className="px-3 py-2 border-b border-gray-100 flex gap-2 text-sm">
                <span className="font-semibold text-gray-500 flex-shrink-0">{sec.heading}:</span>
                <span className={`text-gray-700 ${PROSE_BASE} prose-p:my-0 prose-p:inline`}>
                  <Markdown remarkPlugins={[remarkGfm]}>{contentText}</Markdown>
                </span>
              </div>
            )
          }
          if (!contentText && sec.heading) return null
          const collapsible = isCollapsibleSection(sec.heading)
          return (
            <CollapsibleSection key={si} heading={sec.heading} defaultExpanded={!collapsible}>
              <div className={`text-sm text-gray-700 ${PROSE_COMPACT}`}>
                <Markdown remarkPlugins={[remarkGfm]}>{contentText}</Markdown>
              </div>
            </CollapsibleSection>
          )
        })}

        {/* TodoWrite step list — single source of truth for progress */}
        {isViewingCurrent && todoItems.length > 0 && (
          <div className="p-3 border-t border-gray-100">
            <h4 className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">
              {t('tasks.steps', 'Steps')}
            </h4>
            <div className="space-y-2">
              {todoItems.map((ti, idx) => {
                const isDone = ti.status === 'completed'
                const isActive = ti.status === 'in_progress'
                return (
                  <div key={idx} className={`flex gap-3 p-2 rounded-lg transition-all duration-300 ${
                    isActive ? 'bg-indigo-50 border border-indigo-200 shadow-sm' :
                    isDone ? 'bg-green-50/50 border border-green-100' :
                    'bg-gray-50 border border-transparent'
                  }`}>
                    <div className={`flex-shrink-0 w-6 h-6 rounded-full flex items-center justify-center text-xs font-bold ${
                      isDone ? 'bg-green-500 text-white' :
                      isActive ? 'bg-indigo-500 text-white animate-pulse' :
                      'bg-gray-200 text-gray-500'
                    }`}>
                      {isDone ? '\u2713' : idx + 1}
                    </div>
                    <div className={`flex-1 text-sm ${isDone ? 'text-gray-400 line-through' : isActive ? 'text-indigo-800 font-medium' : 'text-gray-700'}`}>
                      {ti.content}
                    </div>
                  </div>
                )
              })}
            </div>
          </div>
        )}
      </div>

      {showConfirm && (
        <div className="bg-indigo-50/50 border-t border-indigo-100 px-4 py-3 flex items-center gap-2">
          <span className="text-sm text-gray-600 flex-1">
            {t('tasks.planReadyConfirm')}
          </span>
          {onRequestChanges && (
            <button
              onClick={() => {
                const elapsed = Date.now() - planShownAt.current
                sendEvent(FrontendEvent.PLAN_CHANGES_REQUESTED, {
                  revision_count: plan.version,
                  time_to_request_bucket: durationBucket(elapsed),
                  time_to_request_seconds: Math.floor(elapsed / 1000),
                })
                hadChangesRequest.current = true
                onRequestChanges()
              }}
              className="px-4 py-1.5 text-xs border border-gray-300 text-gray-600 rounded hover:bg-gray-100"
            >
              {t('tasks.requestChanges')}
            </button>
          )}
          <button
            onClick={() => {
              const elapsed = Date.now() - planShownAt.current
              sendEvent(FrontendEvent.PLAN_APPROVED, {
                time_to_approve_bucket: durationBucket(elapsed),
                time_to_approve_seconds: Math.floor(elapsed / 1000),
                had_changes_request_first: hadChangesRequest.current,
                revision_count: plan.version,
              })
              if (onConfirm) onConfirm()
            }}
            className="px-4 py-1.5 text-xs bg-green-600 text-white rounded hover:bg-green-700 font-medium"
          >
            {t('tasks.confirmAndExecute')}
          </button>
        </div>
      )}
    </div>
  )
}
