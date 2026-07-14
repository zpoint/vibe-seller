import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { api } from '../api'
import { sendEvent } from '../lib/telemetry'
import { FrontendEvent } from '../lib/telemetryEvents'
import type { Schedule, SchedulePhaseMode } from '../types'
import {
  ScheduleForm,
  type ScheduleFormState,
} from './ScheduleForm'

interface CreateScheduleModalProps {
  storeId: string | null
  storeName?: string
  onClose: () => void
  onCreated: (schedule: Schedule) => void
}

function initialState(defaultTimezone: string): ScheduleFormState {
  return {
    title: '',
    description: '',
    scheduleType: 'days',
    intervalValue: 1,
    scheduleTime: '09:00',
    dayOfWeek: 1,
    dayOfMonth: 1,
    timezone: defaultTimezone,
  }
}

export function CreateScheduleModal({
  storeId,
  storeName,
  onClose,
  onCreated,
}: CreateScheduleModalProps) {
  const { t } = useTranslation()
  // Start with the browser zone so the picker isn't empty while settings load.
  const browserZone = Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'
  const [form, setForm] = useState<ScheduleFormState>(
    initialState(browserZone),
  )
  // phase_mode only meaningful for all-stores schedules; store-bound
  // schedules always resolve to 'single' on the backend regardless.
  const [phaseMode, setPhaseMode] = useState<SchedulePhaseMode>(
    storeId ? 'single' : 'fanout',
  )
  const [creating, setCreating] = useState(false)

  const showModeSelector = storeId === null

  // Pull workspace-level defaults: timezone (when the picker is still
  // on the browser-zone seed) and schedule phase_mode for all-stores
  // create flows. Single fetch covers both.
  useEffect(() => {
    api
      .get('/api/settings')
      .then((s: Record<string, string>) => {
        const tz = s.default_schedule_timezone
        if (tz) {
          setForm(prev =>
            prev.timezone === browserZone ? { ...prev, timezone: tz } : prev,
          )
        }
        if (showModeSelector) {
          const pref = s.default_schedule_phase_mode
          if (pref === 'fanout' || pref === 'single') setPhaseMode(pref)
        }
      })
      .catch(() => {})
    // browserZone is captured at mount; showModeSelector only flips
    // if storeId changes, which remounts this component anyway.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const handleSubmit = async () => {
    if (!form.title.trim() || creating) return
    setCreating(true)
    try {
      const body: Record<string, unknown> = {
        store_id: storeId,
        title: form.title.trim(),
        description: form.description.trim() || null,
        schedule_type: form.scheduleType,
        schedule_time: form.scheduleTime,
        interval_value: form.intervalValue,
        schedule_day:
          form.scheduleType === 'weekly'
            ? form.dayOfWeek
            : form.scheduleType === 'monthly'
              ? form.dayOfMonth
              : null,
        timezone: form.timezone,
      }
      if (showModeSelector) body.phase_mode = phaseMode
      const schedule = await api.post('/api/schedules', body)
      sendEvent(FrontendEvent.SCHEDULE_CREATED, {
        schedule_type: form.scheduleType,
        phase_mode: showModeSelector ? phaseMode : 'single',
        is_store_scoped: !!storeId,
      })
      onCreated(schedule)
      onClose()
    } catch {
      /* ignore */
    }
    setCreating(false)
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-end sm:items-center justify-center bg-black/40"
      onClick={e => {
        if (e.target === e.currentTarget) onClose()
      }}
    >
      <div
        className="bg-white rounded-t-2xl sm:rounded-xl shadow-2xl w-full sm:max-w-lg sm:mx-4 max-h-[92vh] overflow-y-auto"
        onClick={e => e.stopPropagation()}
      >
        <div className="px-6 py-4 border-b border-gray-200">
          <h3 className="text-lg font-semibold">
            {storeId && storeName
              ? t('schedules.newScheduleFor', { storeName })
              : t('schedules.newSchedule')}
          </h3>
          <p className="text-sm text-gray-500 mt-0.5">
            {t('schedules.newScheduleSubtitle')}
          </p>
        </div>
        <ScheduleForm value={form} onChange={setForm} />
        {showModeSelector && (
          <fieldset className="px-6 pb-4 space-y-2">
            <legend className="block text-sm font-medium text-gray-700 mb-1">
              {t('schedules.mode')}
            </legend>
            <label className="flex items-start gap-2 p-2 border border-gray-200 rounded-lg cursor-pointer hover:bg-gray-50">
              <input
                type="radio"
                name="schedule-phase-mode"
                className="mt-1"
                checked={phaseMode === 'fanout'}
                onChange={() => setPhaseMode('fanout')}
              />
              <div className="flex-1">
                <div className="text-sm font-medium text-gray-900">
                  {t('schedules.fanoutMode')}
                </div>
                <div className="text-xs text-gray-500">
                  {t('schedules.fanoutModeDescription')}
                </div>
              </div>
            </label>
            <label className="flex items-start gap-2 p-2 border border-gray-200 rounded-lg cursor-pointer hover:bg-gray-50">
              <input
                type="radio"
                name="schedule-phase-mode"
                className="mt-1"
                checked={phaseMode === 'single'}
                onChange={() => setPhaseMode('single')}
              />
              <div className="flex-1">
                <div className="text-sm font-medium text-gray-900">
                  {t('schedules.singleMode')}
                </div>
                <div className="text-xs text-gray-500">
                  {t('schedules.singleModeDescription')}
                </div>
              </div>
            </label>
          </fieldset>
        )}
        <div className="px-6 py-4 border-t border-gray-200 flex justify-end gap-3">
          <button
            onClick={onClose}
            className="px-4 py-2 text-sm text-gray-700 hover:text-gray-900 bg-gray-100 hover:bg-gray-200 rounded-lg transition-colors"
          >
            {t('common.cancel')}
          </button>
          <button
            onClick={handleSubmit}
            disabled={!form.title.trim() || creating}
            className="px-4 py-2 text-sm font-medium text-white bg-indigo-600 hover:bg-indigo-700 rounded-lg disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            {creating ? t('common.loading') : t('common.create')}
          </button>
        </div>
      </div>
    </div>
  )
}
