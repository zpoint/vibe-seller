/**
 * SchedulePlanPanel tests — cover the 4 render states + history
 * disclosure + CTA wiring.
 */
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { I18nextProvider } from 'react-i18next'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { SchedulePlanPanel } from '../components/SchedulePlanPanel'
import { api } from '../api'
import { i18nTestInstance } from '../test/helpers'
import type { Schedule } from '../types'

vi.mock('../api', () => ({
  api: { get: vi.fn() },
}))

function sched(overrides: Partial<Schedule> = {}): Schedule {
  return {
    id: 'sched-1',
    store_id: null,
    title: 'T',
    description: null,
    platform: null,
    country: null,
    plan: null,
    schedule_type: 'days',
    schedule_time: '09:00',
    schedule_day: null,
    interval_value: 1,
    timezone: 'UTC',
    is_active: true,
    phase_mode: 'fanout',
    plan_mode: true,
    finalize_description: null,
    ai_profile_id: 'default',
    created_by: 'u',
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    next_run: null,
    child_task_count: 0,
    last_run_status: null,
    is_system: false,
    plan_status: 'none',
    plan_version: 0,
    plan_error: null,
    current_planning_task_id: null,
    pending_questions_count: 0,
    ...overrides,
  }
}

function renderPanel(schedule: Schedule, handlers: {
  onOpenTask?: (id: string) => void
  onReplan?: (id: string) => Promise<void> | void
} = {}) {
  return render(
    <I18nextProvider i18n={i18nTestInstance}>
      <SchedulePlanPanel
        schedule={schedule}
        onOpenTask={handlers.onOpenTask || (() => {})}
        onReplan={handlers.onReplan || (() => {})}
      />
    </I18nextProvider>,
  )
}

const mockGet = api.get as ReturnType<typeof vi.fn>

beforeEach(() => {
  mockGet.mockReset()
})

describe('SchedulePlanPanel', () => {
  it('renders nothing for system schedules', () => {
    renderPanel(sched({ is_system: true }))
    expect(screen.queryByTestId('schedule-plan-panel')).toBeNull()
  })

  it('renders nothing for non-plan-mode schedules', () => {
    renderPanel(sched({ plan_mode: false }))
    expect(screen.queryByTestId('schedule-plan-panel')).toBeNull()
  })

  describe('state: none', () => {
    beforeEach(() => {
      mockGet.mockResolvedValue({
        plan_status: 'none',
        plan_version: 0,
        plan_text: null,
        plan_error: null,
        current_planning_task_id: null,
        planning_task_history: [],
      })
    })

    it('shows Start planning CTA', async () => {
      renderPanel(sched())
      const btn = await screen.findByTestId('plan-start')
      expect(btn.textContent).toContain('Start planning')
      expect(screen.queryByTestId('plan-markdown')).toBeNull()
    })

    it('invokes onReplan with schedule id when clicked', async () => {
      const onReplan = vi.fn().mockResolvedValue(undefined)
      renderPanel(sched(), { onReplan })
      const btn = await screen.findByTestId('plan-start')
      fireEvent.click(btn)
      await waitFor(() => expect(onReplan).toHaveBeenCalledWith('sched-1'))
    })
  })

  describe('state: planning', () => {
    beforeEach(() => {
      mockGet.mockResolvedValue({
        plan_status: 'planning',
        plan_version: 0,
        plan_text: null,
        plan_error: null,
        current_planning_task_id: 'plan-task-99',
        planning_task_history: [
          {
            id: 'plan-task-99',
            status: 'designing',
            created_at: new Date().toISOString(),
            completed_at: null,
            error: null,
          },
        ],
      })
    })

    it('shows Open planning task CTA', async () => {
      renderPanel(
        sched({ plan_status: 'planning', current_planning_task_id: 'plan-task-99' }),
      )
      const btn = await screen.findByTestId('plan-open-planning-task')
      expect(btn.textContent).toContain('Open planning task')
    })

    it('invokes onOpenTask with the planning task id', async () => {
      const onOpenTask = vi.fn()
      renderPanel(
        sched({ plan_status: 'planning', current_planning_task_id: 'plan-task-99' }),
        { onOpenTask },
      )
      const btn = await screen.findByTestId('plan-open-planning-task')
      fireEvent.click(btn)
      expect(onOpenTask).toHaveBeenCalledWith('plan-task-99')
    })
  })

  describe('state: ready', () => {
    beforeEach(() => {
      mockGet.mockResolvedValue({
        plan_status: 'ready',
        plan_version: 2,
        plan_text: '## Daily check\n\n1. List URLs\n2. Verify cart',
        plan_error: null,
        current_planning_task_id: null,
        planning_task_history: [
          {
            id: 'prev-1',
            status: 'completed',
            created_at: new Date().toISOString(),
            completed_at: new Date().toISOString(),
            error: null,
          },
        ],
      })
    })

    it('renders the plan markdown when user expands', async () => {
      renderPanel(sched({ plan_status: 'ready', plan_version: 2, plan: '## Daily check\n\n1. List URLs\n2. Verify cart' }))
      // Plan is collapsed by default — click the disclosure to expand.
      const toggle = await screen.findByTestId('plan-toggle')
      fireEvent.click(toggle)
      await screen.findByTestId('plan-markdown')
      // The heading becomes an <h2>, list items become <li>.
      expect(screen.getByText('Daily check')).toBeTruthy()
      expect(screen.getByText('List URLs')).toBeTruthy()
    })

    it('plan markdown is collapsed by default', async () => {
      renderPanel(sched({ plan_status: 'ready', plan_version: 2, plan: '## Daily check' }))
      await screen.findByTestId('plan-toggle')
      expect(screen.queryByTestId('plan-markdown')).toBeNull()
    })

    it('resets planExpanded when switching to a different schedule', async () => {
      // Render panel for schedule A, expand the plan, then switch
      // to schedule B. B should start collapsed-by-default.
      const { rerender } = render(
        <I18nextProvider i18n={i18nTestInstance}>
          <SchedulePlanPanel
            schedule={sched({ id: 'sched-A', plan_status: 'ready', plan_version: 1, plan: '## A' })}
            onOpenTask={() => {}}
            onReplan={() => {}}
          />
        </I18nextProvider>,
      )
      const toggleA = await screen.findByTestId('plan-toggle')
      fireEvent.click(toggleA)
      await screen.findByTestId('plan-markdown')

      // Switch to schedule B (different id).
      rerender(
        <I18nextProvider i18n={i18nTestInstance}>
          <SchedulePlanPanel
            schedule={sched({ id: 'sched-B', plan_status: 'ready', plan_version: 1, plan: '## B' })}
            onOpenTask={() => {}}
            onReplan={() => {}}
          />
        </I18nextProvider>,
      )
      await screen.findByTestId('plan-toggle')
      // B's markdown must NOT be visible — the panel should have
      // reset planExpanded to collapsed.
      expect(screen.queryByTestId('plan-markdown')).toBeNull()
    })

    it('shows Re-plan CTA (not Start planning) when ready', async () => {
      renderPanel(sched({ plan_status: 'ready', plan_version: 2 }))
      const btn = await screen.findByTestId('plan-replan')
      expect(btn.textContent).toContain('Re-plan')
      expect(screen.queryByTestId('plan-start')).toBeNull()
    })

    it('plan version pill is visible in the header', async () => {
      renderPanel(sched({ plan_status: 'ready', plan_version: 2 }))
      // Version pill is rendered in the header row, not gated by
      // the expand toggle.
      await screen.findByText(/Plan version\s*2/)
    })
  })

  describe('state: stale', () => {
    beforeEach(() => {
      mockGet.mockResolvedValue({
        plan_status: 'stale',
        plan_version: 1,
        plan_text: '## prior plan',
        plan_error: null,
        current_planning_task_id: null,
        planning_task_history: [],
      })
    })

    it('shows Re-plan CTA; prior plan text available behind toggle', async () => {
      renderPanel(sched({ plan_status: 'stale', plan: '## prior plan' }))
      const btn = await screen.findByTestId('plan-replan')
      expect(btn.textContent).toContain('Re-plan')
      const toggle = await screen.findByTestId('plan-toggle')
      fireEvent.click(toggle)
      await screen.findByTestId('plan-markdown')
    })
  })

  describe('state: failed', () => {
    beforeEach(() => {
      mockGet.mockResolvedValue({
        plan_status: 'failed',
        plan_version: 0,
        plan_text: null,
        plan_error: 'LLM quota exceeded',
        current_planning_task_id: null,
        planning_task_history: [],
      })
    })

    it('shows plan_error inline and Re-plan CTA', async () => {
      renderPanel(sched({ plan_status: 'failed', plan_error: 'LLM quota exceeded' }))
      await screen.findByTestId('plan-replan')
      expect(screen.getByText(/LLM quota exceeded/)).toBeTruthy()
    })
  })

  describe('history disclosure', () => {
    it('expands to show historical planning tasks', async () => {
      mockGet.mockResolvedValue({
        plan_status: 'ready',
        plan_version: 2,
        plan_text: '## plan',
        plan_error: null,
        current_planning_task_id: null,
        planning_task_history: [
          { id: 'newest', status: 'completed', created_at: '2026-04-01T00:00:00Z', completed_at: '2026-04-01T00:01:00Z', error: null },
          { id: 'older', status: 'failed', created_at: '2026-03-01T00:00:00Z', completed_at: null, error: 'boom' },
        ],
      })
      renderPanel(sched({ plan_status: 'ready' }))
      // Wait for fetch then click the disclosure.
      const toggle = await screen.findByTestId('plan-history-toggle')
      fireEvent.click(toggle)
      const list = await screen.findByTestId('plan-history-list')
      expect(list.textContent).toContain('completed')
      expect(list.textContent).toContain('failed')
      expect(list.textContent).toContain('boom')
    })
  })

  describe('finalize step', () => {
    it('renders the finalize card when the agent registered one', async () => {
      mockGet.mockResolvedValue({
        plan_status: 'ready',
        plan_version: 1,
        plan_text: '## plan',
        plan_error: null,
        current_planning_task_id: null,
        planning_task_history: [],
        finalize_description: 'Combine all stores into ONE PR + WeCom.',
      })
      renderPanel(sched({ plan_status: 'ready' }))
      const card = await screen.findByTestId('schedule-finalize-step')
      expect(card.textContent).toContain('Combine all stores into ONE PR')
    })

    it('hides the finalize card when none is registered', async () => {
      mockGet.mockResolvedValue({
        plan_status: 'ready',
        plan_version: 1,
        plan_text: '## plan',
        plan_error: null,
        current_planning_task_id: null,
        planning_task_history: [],
        finalize_description: null,
      })
      renderPanel(sched({ plan_status: 'ready' }))
      await screen.findByTestId('plan-history-toggle')
      expect(screen.queryByTestId('schedule-finalize-step')).toBeNull()
    })
  })
})
