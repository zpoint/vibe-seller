/**
 * F10-F12: ScheduleList component tests.
 */
import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { I18nextProvider } from 'react-i18next'
import { ScheduleList } from '../components/ScheduleList'
import { i18nTestInstance } from '../test/helpers'
import type { Schedule, Store } from '../types'

function makeSchedule(overrides: Partial<Schedule> = {}): Schedule {
  return {
    id: `sched-${Math.random().toString(36).slice(2)}`,
    store_id: null,
    title: 'Test Schedule',
    description: null,
    platform: null,
    country: null,
    plan: null,
    schedule_type: 'daily',
    schedule_time: '09:00',
    schedule_day: null,
    interval_value: 1,
    timezone: 'UTC',
    is_active: true,
    plan_mode: false,
    ai_profile_id: null,
    created_by: 'user1',
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    next_run: null,
    child_task_count: 0,
    last_run_status: null,
    ...overrides,
  }
}

const defaultProps = {
  selectedSchedule: null,
  selectSchedule: vi.fn(),
  setShowCreateSchedule: vi.fn(),
  getScheduleBadge: (s: Schedule) => `Daily ${s.schedule_time?.slice(0, 5) || ''}`,
}

function renderScheduleList(overrides: Record<string, unknown> = {}) {
  const stores: Store[] = (overrides.stores as Store[]) || [
    { id: 'store1', name: 'Amazon US', browser_backend: 'chrome', browser_config: {}, ziniao_account_id: null, browser_oauth: null, platforms: [], countries: [], platform_countries: {}, created_at: '', updated_at: '' },
  ]

  return render(
    <I18nextProvider i18n={i18nTestInstance}>
      <ScheduleList
        schedules={(overrides.schedules as Schedule[]) || []}
        selectedSchedule={(overrides.selectedSchedule as Schedule | null) ?? defaultProps.selectedSchedule}
        showAllTasks={(overrides.showAllTasks as boolean) ?? true}
        selectedStore={(overrides.selectedStore as Store | null) ?? null}
        stores={stores}
        selectSchedule={defaultProps.selectSchedule}
        setShowCreateSchedule={defaultProps.setShowCreateSchedule}
        getScheduleBadge={defaultProps.getScheduleBadge}
      />
    </I18nextProvider>,
  )
}

describe('F10: showAllTasks=true → all schedule types visible', () => {
  it('renders all-stores and store-specific schedules', () => {
    const schedules = [
      makeSchedule({ title: 'All Stores Sched', store_id: null }),
      makeSchedule({ title: 'Store Sched', store_id: 'store1' }),
    ]

    renderScheduleList({ schedules, showAllTasks: true })

    expect(screen.getByText('All Stores Sched')).toBeInTheDocument()
    expect(screen.getByText('Store Sched')).toBeInTheDocument()
  })

  it('shows All Stores badge for schedules without store_id', () => {
    const schedules = [
      makeSchedule({ title: 'My All Stores', store_id: null }),
    ]

    const { container } = renderScheduleList({ schedules, showAllTasks: true })

    // Badge with purple bg
    const badge = container.querySelector('.bg-indigo-100')
    expect(badge).toBeInTheDocument()
    expect(badge?.textContent).toBe('All Stores')
  })

  it('shows store name for store-specific schedules', () => {
    const schedules = [
      makeSchedule({ title: 'Store Task', store_id: 'store1' }),
    ]

    renderScheduleList({ schedules, showAllTasks: true })

    expect(screen.getByText('Amazon US')).toBeInTheDocument()
  })
})

describe('F11: showAllTasks=false → store-specific + all-stores', () => {
  it('shows store-specific and all-stores schedules', () => {
    const store: Store = { id: 'store1', name: 'Amazon US', browser_backend: 'chrome', browser_config: {}, ziniao_account_id: null, browser_oauth: null, platforms: [], countries: [], platform_countries: {}, created_at: '', updated_at: '' }
    const schedules = [
      makeSchedule({ title: 'All Stores Visible', store_id: null }),
      makeSchedule({ title: 'Store Visible', store_id: 'store1' }),
    ]

    renderScheduleList({ schedules, showAllTasks: false, selectedStore: store })

    expect(screen.getByText('Store Visible')).toBeInTheDocument()
    expect(screen.getByText('All Stores Visible')).toBeInTheDocument()
  })
})

describe('F12: All-stores grouping', () => {
  it('shows runs count for all-stores schedules', () => {
    const schedules = [
      makeSchedule({ title: 'Daily All Stores', store_id: null, child_task_count: 5 }),
    ]

    const { container } = renderScheduleList({ schedules, showAllTasks: true })

    // "5 Runs" rendered as children in a div
    const runsDiv = Array.from(container.querySelectorAll('div'))
      .find(el => el.textContent?.includes('5') && el.textContent?.includes('Runs'))
    expect(runsDiv).toBeTruthy()
  })
})
