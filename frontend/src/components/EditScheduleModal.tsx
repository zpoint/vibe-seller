import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { api } from '../api'
import { sendEvent } from '../lib/telemetry'
import { FrontendEvent } from '../lib/telemetryEvents'
import type { Schedule } from '../types'
import {
  ScheduleForm,
  type ScheduleFormState,
  type ScheduleType,
} from './ScheduleForm'

interface EditScheduleModalProps {
  schedule: Schedule
  onClose: () => void
  onUpdated: (schedule: Schedule) => void
}

function fromSchedule(s: Schedule): ScheduleFormState {
  return {
    title: s.title,
    description: s.description || '',
    scheduleType: s.schedule_type as ScheduleType,
    intervalValue: s.interval_value,
    scheduleTime: s.schedule_time || '09:00',
    dayOfWeek:
      s.schedule_type === 'weekly' && s.schedule_day != null
        ? s.schedule_day
        : 1,
    dayOfMonth:
      s.schedule_type === 'monthly' && s.schedule_day != null
        ? s.schedule_day
        : 1,
    timezone:
      s.timezone || Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC',
  }
}

export function EditScheduleModal({
  schedule,
  onClose,
  onUpdated,
}: EditScheduleModalProps) {
  const { t } = useTranslation()
  const [form, setForm] = useState<ScheduleFormState>(fromSchedule(schedule))
  const [saving, setSaving] = useState(false)

  const handleSubmit = async () => {
    if (!form.title.trim() || saving) return
    setSaving(true)
    try {
      const body: Record<string, unknown> = {
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
      const updated = await api.put(
        `/api/schedules/${schedule.id}`,
        body,
      )
      sendEvent(FrontendEvent.SCHEDULE_EDITED, {
        schedule_type: form.scheduleType,
        is_store_scoped: !!schedule.store_id,
      })
      onUpdated(updated)
      onClose()
    } catch {
      /* ignore */
    }
    setSaving(false)
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
            {t('schedules.editSchedule')}
          </h3>
        </div>
        <ScheduleForm value={form} onChange={setForm} />
        <div className="px-6 py-4 border-t border-gray-200 flex justify-end gap-3">
          <button
            onClick={onClose}
            className="px-4 py-2 text-sm text-gray-700 hover:text-gray-900 bg-gray-100 hover:bg-gray-200 rounded-lg transition-colors"
          >
            {t('common.cancel')}
          </button>
          <button
            onClick={handleSubmit}
            disabled={!form.title.trim() || saving}
            className="px-4 py-2 text-sm font-medium text-white bg-indigo-600 hover:bg-indigo-700 rounded-lg disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            {saving ? t('common.loading') : t('common.save')}
          </button>
        </div>
      </div>
    </div>
  )
}
