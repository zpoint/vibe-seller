import { describe, it, expect } from 'vitest'
import i18n from 'i18next'
import { initReactI18next } from 'react-i18next'
import enTranslation from '../i18n/locales/en/translation.json'
import { formatScheduleBadge } from '../lib/scheduleBadge'
import type { Schedule } from '../types'

const inst = i18n.createInstance()
inst.use(initReactI18next).init({
  resources: { en: { translation: enTranslation } },
  lng: 'en',
  fallbackLng: 'en',
  interpolation: { escapeValue: false },
})
const t = (k: string) => inst.t(k)

function sched(overrides: Partial<Schedule>): Schedule {
  return {
    id: 's',
    store_id: null,
    title: 'x',
    description: null,
    platform: null,
    country: null,
    plan: null,
    schedule_type: 'days',
    schedule_time: '04:00',
    schedule_day: null,
    interval_value: 1,
    timezone: 'UTC',
    is_active: true,
    plan_mode: false,
    ai_profile_id: null,
    created_by: 'u',
    created_at: '',
    updated_at: '',
    next_run: null,
    child_task_count: 0,
    last_run_status: null,
    ...overrides,
  } as Schedule
}

describe('formatScheduleBadge — honours interval_value for every type', () => {
  it('weekly every 2 weeks reads "Every 2 weeks", not "Weekly"', () => {
    const badge = formatScheduleBadge(
      sched({ schedule_type: 'weekly', interval_value: 2, schedule_day: 1 }),
      t,
    )
    // The bug: this used to render "Weekly Mon 04:00", hiding the 2.
    expect(badge).toBe('Every 2 weeks Monday 04:00')
    expect(badge).not.toMatch(/^Weekly/)
  })

  it('weekly every 1 week still reads "Weekly"', () => {
    expect(
      formatScheduleBadge(
        sched({ schedule_type: 'weekly', interval_value: 1, schedule_day: 3 }),
        t,
      ),
    ).toBe('Weekly Wednesday 04:00')
  })

  it('monthly every 3 months reads "Every 3 months", not "Monthly"', () => {
    const badge = formatScheduleBadge(
      sched({ schedule_type: 'monthly', interval_value: 3, schedule_day: 15 }),
      t,
    )
    expect(badge).toBe('Every 3 months 15 04:00')
    expect(badge).not.toMatch(/^Monthly/)
  })

  it('monthly every 1 month still reads "Monthly"', () => {
    expect(
      formatScheduleBadge(
        sched({ schedule_type: 'monthly', interval_value: 1, schedule_day: 1 }),
        t,
      ),
    ).toBe('Monthly 1 04:00')
  })

  it('days/minutes/hours unchanged', () => {
    expect(
      formatScheduleBadge(
        sched({ schedule_type: 'days', interval_value: 3 }),
        t,
      ),
    ).toBe('Every 3 days 04:00')
    expect(
      formatScheduleBadge(
        sched({ schedule_type: 'days', interval_value: 1 }),
        t,
      ),
    ).toBe('Daily 04:00')
    expect(
      formatScheduleBadge(
        sched({ schedule_type: 'minutes', interval_value: 15 }),
        t,
      ),
    ).toBe('Every 15 minutes')
  })
})
