import { useTranslation } from 'react-i18next'
import { TimezoneSelect } from './TimezoneSelect'

export type ScheduleType =
  | 'minutes'
  | 'hours'
  | 'days'
  | 'weekly'
  | 'monthly'

export interface ScheduleFormState {
  title: string
  description: string
  scheduleType: ScheduleType
  intervalValue: number
  scheduleTime: string
  dayOfWeek: number
  dayOfMonth: number
  timezone: string
}

interface ScheduleFormProps {
  value: ScheduleFormState
  onChange: (next: ScheduleFormState) => void
  showTitle?: boolean
}

export function ScheduleForm({
  value,
  onChange,
  showTitle = true,
}: ScheduleFormProps) {
  const { t } = useTranslation()
  const set = <K extends keyof ScheduleFormState>(
    k: K,
    v: ScheduleFormState[K],
  ) => onChange({ ...value, [k]: v })

  const DAYS = [
    { value: 1, label: t('schedules.mon') },
    { value: 2, label: t('schedules.tue') },
    { value: 3, label: t('schedules.wed') },
    { value: 4, label: t('schedules.thu') },
    { value: 5, label: t('schedules.fri') },
    { value: 6, label: t('schedules.sat') },
    { value: 7, label: t('schedules.sun') },
  ]

  const showTime =
    value.scheduleType === 'days' ||
    value.scheduleType === 'weekly' ||
    value.scheduleType === 'monthly'

  const pad2 = (n: number) => String(n).padStart(2, '0')
  const [hourStr, minuteStr] = (value.scheduleTime || '09:00').split(':')
  const selectedHour = Math.max(0, Math.min(23, Number(hourStr) || 0))
  const selectedMinute = Math.max(0, Math.min(59, Number(minuteStr) || 0))
  const setTime = (h: number, m: number) =>
    set('scheduleTime', `${pad2(h)}:${pad2(m)}`)

  return (
    <div className="px-6 py-4 space-y-4">
      {showTitle && (
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">
            {t('tasks.titleLabel')}
          </label>
          <input
            value={value.title}
            onChange={e => set('title', e.target.value)}
            placeholder={t('tasks.titlePlaceholder')}
            className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
            autoFocus
          />
        </div>
      )}
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">
          {t('schedules.description')}{' '}
          <span className="text-gray-400 font-normal">
            ({t('common.optional')})
          </span>
        </label>
        <textarea
          value={value.description}
          onChange={e => set('description', e.target.value)}
          rows={2}
          placeholder={t('tasks.descriptionPlaceholder')}
          className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm resize-none focus:outline-none focus:ring-2 focus:ring-indigo-500"
        />
      </div>

      {/* Interval picker: Every [N] [unit] */}
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">
          {t('schedules.frequency')}
        </label>
        <div className="flex items-center gap-2">
          <span className="text-sm text-gray-600 shrink-0">
            {t('schedules.every')}
          </span>
          <input
            type="number"
            min={1}
            max={
              value.scheduleType === 'minutes'
                ? 1440
                : value.scheduleType === 'hours'
                  ? 720
                  : 365
            }
            value={value.intervalValue}
            onChange={e =>
              set('intervalValue', Math.max(1, Number(e.target.value)))
            }
            className="w-20 px-2 py-2 border border-gray-300 rounded-lg text-sm text-center"
          />
          <select
            value={value.scheduleType}
            onChange={e =>
              onChange({
                ...value,
                scheduleType: e.target.value as ScheduleType,
                intervalValue: 1,
              })
            }
            className="flex-1 px-3 py-2 border border-gray-300 rounded-lg text-sm bg-white"
          >
            <option value="minutes">{t('schedules.minutes')}</option>
            <option value="hours">{t('schedules.hours')}</option>
            <option value="days">{t('schedules.days')}</option>
            <option value="weekly">{t('schedules.weeks')}</option>
            <option value="monthly">{t('schedules.months')}</option>
          </select>
        </div>
      </div>

      {/* Time picker (for days/weekly/monthly) */}
      {showTime && (
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">
            {t('schedules.atTime')}
          </label>
          <div className="flex items-center gap-2">
            <select
              value={selectedHour}
              onChange={e => setTime(Number(e.target.value), selectedMinute)}
              className="flex-1 px-3 py-2 border border-gray-300 rounded-lg text-sm bg-white"
            >
              {Array.from({ length: 24 }, (_, h) => (
                <option key={h} value={h}>
                  {pad2(h)} {t('schedules.hourSuffix')}
                </option>
              ))}
            </select>
            <span className="text-gray-400">:</span>
            <select
              value={selectedMinute}
              onChange={e => setTime(selectedHour, Number(e.target.value))}
              className="flex-1 px-3 py-2 border border-gray-300 rounded-lg text-sm bg-white"
            >
              {Array.from({ length: 60 }, (_, m) => m).map(m => (
                <option key={m} value={m}>
                  {pad2(m)} {t('schedules.minuteSuffix')}
                </option>
              ))}
            </select>
          </div>
        </div>
      )}

      {/* Day of week (for weekly) */}
      {value.scheduleType === 'weekly' && (
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">
            {t('schedules.dayOfWeek')}
          </label>
          <select
            value={value.dayOfWeek}
            onChange={e => set('dayOfWeek', Number(e.target.value))}
            className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm bg-white"
          >
            {DAYS.map(d => (
              <option key={d.value} value={d.value}>
                {d.label}
              </option>
            ))}
          </select>
        </div>
      )}

      {/* Day of month (for monthly) */}
      {value.scheduleType === 'monthly' && (
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">
            {t('schedules.dayOfMonth')}
          </label>
          <input
            type="number"
            min={1}
            max={31}
            value={value.dayOfMonth}
            onChange={e => set('dayOfMonth', Number(e.target.value))}
            className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm"
          />
        </div>
      )}

      {/* Timezone */}
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">
          {t('schedules.timezone')}
        </label>
        <TimezoneSelect
          value={value.timezone}
          onChange={tz => set('timezone', tz)}
        />
        <p className="text-xs text-gray-500 mt-1">
          {t('schedules.timezoneHint')}
        </p>
      </div>
    </div>
  )
}
