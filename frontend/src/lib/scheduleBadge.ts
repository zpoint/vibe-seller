import type { Schedule } from '../types'

type TFunc = (key: string) => string

type BadgeFields = Pick<
  Schedule,
  'schedule_type' | 'schedule_time' | 'schedule_day' | 'interval_value'
>

/**
 * Human-readable frequency badge for a schedule.
 *
 * Honours `interval_value` for every schedule type — including weekly
 * and monthly, which previously always rendered "Weekly"/"Monthly" and
 * hid the fact that a user had asked for "every 2 weeks" / "every 3
 * months". Kept as a pure function (no React) so the cadence wording is
 * unit-testable and can't silently drift from the backend trigger.
 */
export function formatScheduleBadge(s: BadgeFields, t: TFunc): string {
  const time = s.schedule_time?.slice(0, 5) || '00:00'
  const n = s.interval_value || 1
  const DAYS = [
    t('schedules.mon'),
    t('schedules.tue'),
    t('schedules.wed'),
    t('schedules.thu'),
    t('schedules.fri'),
    t('schedules.sat'),
    t('schedules.sun'),
  ]
  const every = t('schedules.every')

  switch (s.schedule_type) {
    case 'minutes':
      return `${every} ${n} ${t('schedules.minutes')}`
    case 'hours':
      return `${every} ${n} ${t('schedules.hours')}`
    case 'days':
      return n === 1
        ? `${t('schedules.daily')} ${time}`
        : `${every} ${n} ${t('schedules.days')} ${time}`
    case 'weekly': {
      const day = DAYS[(s.schedule_day || 1) - 1]
      return n === 1
        ? `${t('schedules.weekly')} ${day} ${time}`
        : `${every} ${n} ${t('schedules.weeks')} ${day} ${time}`
    }
    case 'monthly': {
      const dom = s.schedule_day || 1
      return n === 1
        ? `${t('schedules.monthly')} ${dom} ${time}`
        : `${every} ${n} ${t('schedules.months')} ${dom} ${time}`
    }
    case 'daily':
      return `${t('schedules.daily')} ${time}`
    default:
      return `${s.schedule_type} ${time}`
  }
}
