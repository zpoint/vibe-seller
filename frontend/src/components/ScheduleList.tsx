import { useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import type { Schedule, Store } from '../types'

interface ScheduleListProps {
  schedules: Schedule[]
  selectedSchedule: Schedule | null
  showAllTasks: boolean
  selectedStore: Store | null
  stores: Store[]
  selectSchedule: (s: Schedule) => void
  setShowCreateSchedule: (v: boolean) => void
  getScheduleBadge: (s: Schedule) => string
}

const PLAN_STATUS_STYLES: Record<string, string> = {
  planning: 'bg-yellow-100 text-yellow-800',
  stale: 'bg-orange-100 text-orange-800',
  failed: 'bg-red-100 text-red-700',
  none: 'bg-amber-100 text-amber-800',
}

function PlanStatusBadge({ schedule, t }: { schedule: Schedule; t: (k: string) => string }) {
  if (!schedule.plan_mode || schedule.is_system) return null
  const s = schedule.plan_status
  if (s === 'ready') return null
  const cls = PLAN_STATUS_STYLES[s] || 'bg-gray-100 text-gray-600'
  return (
    <span className={`px-1.5 py-0.5 text-[10px] rounded-full font-medium ${cls}`}>
      {t(`schedules.planStatus.${s}`)}
    </span>
  )
}

function ScheduleItem({ schedule, badge, storeName, isSelected, onClick, getScheduleBadge, t }: {
  schedule: Schedule; badge?: string; storeName?: string; isSelected: boolean
  onClick: () => void; getScheduleBadge: (s: Schedule) => string; t: (key: string, opts?: Record<string, unknown>) => string
}) {
  return (
    <button
      onClick={onClick}
      className={`w-full text-left px-4 py-3 hover:bg-gray-50 border-b border-gray-100 ${isSelected ? 'bg-indigo-50' : ''}`}
    >
      <div className="flex items-center gap-2">
        <span className="font-medium text-sm truncate flex-1">{schedule.title}</span>
        {badge && (
          <span className="px-1.5 py-0.5 text-[10px] rounded-full font-medium bg-indigo-100 text-indigo-700 shrink-0">{badge}</span>
        )}
      </div>
      <div className="flex items-center gap-2 mt-1 flex-wrap">
        {schedule.is_system && (
          <span className="px-1.5 py-0.5 text-[10px] rounded-full font-medium bg-indigo-100 text-indigo-700">{t('schedules.system')}</span>
        )}
        <span className={`px-1.5 py-0.5 text-[10px] rounded-full font-medium ${schedule.is_active ? 'bg-green-100 text-green-700' : 'bg-gray-100 text-gray-500'}`}>
          {schedule.is_active ? t('schedules.active') : t('schedules.paused')}
        </span>
        <PlanStatusBadge schedule={schedule} t={t} />
        {schedule.pending_questions_count > 0 && (
          <span className="px-1.5 py-0.5 text-[10px] rounded-full font-medium bg-amber-100 text-amber-800">
            {t('schedules.pendingQuestions', { count: schedule.pending_questions_count })}
          </span>
        )}
        <span className="text-xs text-gray-400">{getScheduleBadge(schedule)}</span>
      </div>
      {storeName && (
        <div className="text-[10px] text-gray-400 mt-0.5">{storeName}</div>
      )}
      {schedule.child_task_count > 0 && (
        <div className="text-xs text-gray-400 mt-0.5">{schedule.child_task_count} {t('schedules.runs')}</div>
      )}
    </button>
  )
}

export function ScheduleList({
  schedules,
  selectedSchedule,
  showAllTasks,
  selectedStore,
  stores,
  selectSchedule,
  setShowCreateSchedule,
  getScheduleBadge,
}: ScheduleListProps) {
  const { t } = useTranslation()

  const storeNameMap = useMemo(() => {
    const map = new Map<string, string>()
    for (const st of stores) {
      map.set(st.id, st.name)
    }
    return map
  }, [stores])

  const allStoresScheds = schedules.filter(s => !s.store_id)
  const storeScheds = showAllTasks
    ? schedules.filter(s => s.store_id)
    : schedules.filter(s => s.store_id === selectedStore?.id)
  const allVisible = showAllTasks
    ? [...allStoresScheds, ...storeScheds]
    : [...storeScheds, ...allStoresScheds]

  return (
    <>
      {showAllTasks && allStoresScheds.map(s => <ScheduleItem key={s.id} schedule={s} badge={t('tasks.allStores')} isSelected={selectedSchedule?.id === s.id} onClick={() => selectSchedule(s)} getScheduleBadge={getScheduleBadge} t={t} />)}
      {showAllTasks && allStoresScheds.length > 0 && storeScheds.length > 0 && (
        <div className="border-b-2 border-gray-200 my-1" />
      )}
      {showAllTasks && storeScheds.map(s => <ScheduleItem key={s.id} schedule={s} storeName={(s.store_id && storeNameMap.get(s.store_id)) || s.store_id || undefined} isSelected={selectedSchedule?.id === s.id} onClick={() => selectSchedule(s)} getScheduleBadge={getScheduleBadge} t={t} />)}

      {!showAllTasks && storeScheds.map(s => <ScheduleItem key={s.id} schedule={s} isSelected={selectedSchedule?.id === s.id} onClick={() => selectSchedule(s)} getScheduleBadge={getScheduleBadge} t={t} />)}
      {!showAllTasks && allStoresScheds.length > 0 && storeScheds.length > 0 && (
        <div className="border-b-2 border-gray-200 my-1" />
      )}
      {!showAllTasks && allStoresScheds.map(s => <ScheduleItem key={s.id} schedule={s} badge={t('tasks.allStores')} isSelected={selectedSchedule?.id === s.id} onClick={() => selectSchedule(s)} getScheduleBadge={getScheduleBadge} t={t} />)}

      {allVisible.length === 0 && (
        <div className="flex flex-col items-center justify-center py-12 px-4 text-center">
          <p className="text-sm text-gray-500 mb-2">{t('schedules.noSchedules')}</p>
          <p className="text-xs text-gray-400 mb-3">{t('schedules.createFirstSchedule')}</p>
          <button
            onClick={() => setShowCreateSchedule(true)}
            className="px-4 py-2 bg-indigo-600 text-white rounded-lg text-sm font-medium hover:bg-indigo-700"
          >
            + {t('schedules.newSchedule')}
          </button>
        </div>
      )}
    </>
  )
}
