import { useTranslation } from 'react-i18next'

import type { Schedule, Store, Task } from '../types'
import { StatusBadge } from './ui'
import { AllStoresTaskList } from './AllStoresTaskList'
import { SchedulePlanPanel } from './SchedulePlanPanel'

interface Props {
  schedule: Schedule
  scheduleTasks: Task[]
  stores: Store[]
  selectedStore: Store | null
  showAllTasks: boolean
  hasProgressingScheduleTask: boolean
  selectTask: (task: Task) => void
  toggleSchedulePause: (id: string, isActive: boolean) => void
  triggerSchedule: (id: string) => void
  replanSchedule: (id: string) => Promise<void> | void
  setEditingSchedule: (s: Schedule) => void
  deleteSchedule: (id: string) => void
  getScheduleBadge: (s: Schedule) => string
  formatDate: (s: string) => string
}

/**
 * The right-pane schedule detail view.
 *
 * Extracted from TasksView.tsx so the file stays under the 800-line
 * pre-commit cap. Layout: header → description → plan panel →
 * metadata grid → action row → runs list.
 */
export function ScheduleDetailView({
  schedule,
  scheduleTasks,
  stores,
  selectedStore,
  showAllTasks,
  hasProgressingScheduleTask,
  selectTask,
  toggleSchedulePause,
  triggerSchedule,
  replanSchedule,
  setEditingSchedule,
  deleteSchedule,
  getScheduleBadge,
  formatDate,
}: Props) {
  const { t } = useTranslation()

  const needsPlan = schedule.plan_mode && !schedule.is_system
  const notReady = needsPlan && schedule.plan_status !== 'ready'
  const triggerDisabled = hasProgressingScheduleTask || notReady
  const triggerTitle = notReady
    ? t(`schedules.planStatus.${schedule.plan_status}`)
    : hasProgressingScheduleTask
      ? t('schedules.triggerRunning')
      : undefined

  return (
    <div className="flex-1 overflow-y-auto p-6">
      <div className="bg-white rounded-lg border border-gray-200 p-6 mb-4">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold">{schedule.title}</h2>
          <div className="flex items-center gap-2">
            {schedule.is_system && (
              <span className="px-2 py-1 text-xs rounded-full font-medium bg-indigo-100 text-indigo-700">
                {t('schedules.system')}
              </span>
            )}
            <span
              className={`px-2 py-1 text-xs rounded-full font-medium ${schedule.is_active ? 'bg-green-100 text-green-700' : 'bg-gray-100 text-gray-500'}`}
            >
              {schedule.is_active ? t('schedules.active') : t('schedules.paused')}
            </span>
          </div>
        </div>

        {schedule.description && (
          <p className="text-sm text-gray-600 mb-4">{schedule.description}</p>
        )}

        <SchedulePlanPanel
          schedule={schedule}
          onOpenTask={(id) => {
            // selectTask re-fetches by id immediately, so a stub
            // with just `id` is enough. Plan-only tasks no longer
            // appear in scheduleTasks (filtered server-side).
            selectTask({ id } as Task)
          }}
          onReplan={async (id) => {
            await replanSchedule(id)
          }}
        />

        <div className="grid grid-cols-2 gap-3 text-sm mb-4">
          <div>
            <span className="text-gray-500">{t('schedules.scheduleType')}:</span>
            <span className="ml-2 font-medium">{getScheduleBadge(schedule)}</span>
          </div>
          <div>
            <span className="text-gray-500">{t('schedules.timezone')}:</span>
            <span className="ml-2 font-medium">{schedule.timezone}</span>
          </div>
          {schedule.next_run && (
            <div>
              <span className="text-gray-500">{t('schedules.nextRun')}:</span>
              <span className="ml-2 font-medium">
                {new Date(schedule.next_run).toLocaleString()}
              </span>
            </div>
          )}
          <div>
            <span className="text-gray-500">{t('schedules.runs')}:</span>
            <span className="ml-2 font-medium">{schedule.child_task_count}</span>
          </div>
        </div>

        <div className="flex gap-2">
          <button
            onClick={() => toggleSchedulePause(schedule.id, schedule.is_active)}
            className={`px-3 py-1.5 text-xs rounded font-medium ${schedule.is_active ? 'bg-amber-100 text-amber-700 hover:bg-amber-200' : 'bg-green-100 text-green-700 hover:bg-green-200'}`}
          >
            {schedule.is_active ? t('schedules.pause') : t('schedules.resume')}
          </button>
          <button
            onClick={() => triggerSchedule(schedule.id)}
            disabled={triggerDisabled}
            title={triggerTitle}
            className={`px-3 py-1.5 text-xs rounded font-medium ${triggerDisabled ? 'bg-gray-200 text-gray-400 cursor-not-allowed' : 'bg-indigo-600 text-white hover:bg-indigo-700'}`}
          >
            {t('schedules.trigger')}
          </button>
          {!schedule.is_system && (
            <>
              <button
                onClick={() => setEditingSchedule(schedule)}
                className="px-3 py-1.5 text-xs bg-gray-100 text-gray-700 rounded font-medium hover:bg-gray-200"
              >
                {t('common.edit')}
              </button>
              <button
                onClick={() => {
                  if (confirm(t('schedules.deleteConfirm')))
                    deleteSchedule(schedule.id)
                }}
                className="px-3 py-1.5 text-xs bg-red-100 text-red-700 rounded font-medium hover:bg-red-200"
              >
                {t('common.delete')}
              </button>
            </>
          )}
        </div>
      </div>

      {/* Runs list — plan-only tasks filtered out server-side. */}
      <h3 className="text-sm font-semibold text-gray-600 mb-2">
        {t('schedules.runs')} ({schedule.child_task_count})
      </h3>
      <div className="space-y-2">
        {!schedule.store_id ? (
          <AllStoresTaskList
            scheduleTasks={scheduleTasks}
            stores={stores}
            selectedStore={selectedStore}
            showAllTasks={showAllTasks}
            selectTask={selectTask}
            formatDate={formatDate}
          />
        ) : (
          scheduleTasks.map((task) => (
            <button
              key={task.id}
              onClick={() => selectTask(task)}
              className="w-full text-left bg-white rounded-lg border border-gray-200 p-3 hover:bg-gray-50 transition-colors"
            >
              <div className="flex items-center justify-between">
                <span className="font-medium text-sm truncate">{task.title}</span>
                <StatusBadge status={task.status} />
              </div>
              <div className="text-xs text-gray-400 mt-1">
                {formatDate(task.created_at)}
              </div>
            </button>
          ))
        )}
        {scheduleTasks.length === 0 && (
          <div className="text-sm text-gray-400 text-center py-4">
            {t('common.noData')}
          </div>
        )}
      </div>
    </div>
  )
}
