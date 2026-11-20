/**
 * SchedulePlanPanel tests — cover the render states (incl. the
 * approval wait) + history disclosure + CTA wiring.
 */
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { I18nextProvider } from 'react-i18next'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { SchedulePlanPanel } from '../components/SchedulePlanPanel'
import { api } from '../api'
import { i18nTestInstance } from '../test/helpers'
import type { Schedule } from '../types'

vi.mock('../api', () => ({
  api: { get: vi.fn(), post: vi.fn() },
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
  onUpdated?: (s: Schedule) => void
} = {}) {
  return render(
    <I18nextProvider i18n={i18nTestInstance}>
      <SchedulePlanPanel
        schedule={schedule}
        onOpenTask={handlers.onOpenTask || (() => {})}
        onReplan={handlers.onReplan || (() => {})}
        onUpdated={handlers.onUpdated}
      />
    </I18nextProvider>,
  )
}

const mockGet = api.get as ReturnType<typeof vi.fn>
const mockPost = api.post as ReturnType<typeof vi.fn>

beforeEach(() => {
  mockGet.mockReset()
  mockPost.mockReset()
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

    it('says a known reason in plain words and hides the technical text', async () => {
      mockGet.mockResolvedValue({
        plan_status: 'failed', plan_version: 0, plan_text: null,
        plan_error: 'Server restarted while task was active',
        plan_error_code: 'server_restart',
        current_planning_task_id: null, planning_task_history: [],
      })
      renderPanel(sched({ plan_status: 'failed' }))
      const reason = await screen.findByTestId('plan-failure-reason')
      expect(reason.textContent).toContain('Vibe Seller restarted while the plan was being written')
      expect(screen.queryByTestId('plan-failure-detail')).toBeNull()
      expect(screen.queryByText(/Server restarted while task was active/)).toBeNull()
    })

    it('an unknown cause keeps the detail under the plain sentence', async () => {
      mockGet.mockResolvedValue({
        plan_status: 'failed', plan_version: 0, plan_text: null,
        plan_error: 'LLM quota exceeded', plan_error_code: 'other',
        current_planning_task_id: null, planning_task_history: [],
      })
      renderPanel(sched({ plan_status: 'failed' }))
      const reason = await screen.findByTestId('plan-failure-reason')
      expect(reason.textContent).toContain('Something went wrong while writing the plan')
      expect(screen.getByTestId('plan-failure-detail').textContent).toContain('Details: LLM quota exceeded')
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

  describe('fanout serial', () => {
    const ready = {
      plan_status: 'ready',
      plan_version: 1,
      plan_text: '## plan',
      plan_error: null,
      current_planning_task_id: null,
      planning_task_history: [],
      finalize_description: null,
    }

    it('says so when the schedule runs its stores one at a time', async () => {
      mockGet.mockResolvedValue(ready)
      renderPanel(sched({ plan_status: 'ready', fanout_serial: true }))
      expect(
        (await screen.findByTestId('schedule-fanout-serial')).textContent,
      ).toContain('one at a time')
    })

    it('stays silent for an ordinary fanout', async () => {
      mockGet.mockResolvedValue(ready)
      renderPanel(sched({ plan_status: 'ready', fanout_serial: false }))
      await screen.findByTestId('plan-history-toggle')
      expect(screen.queryByTestId('schedule-fanout-serial')).toBeNull()
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
  describe('state: pending_approval', () => {
    const pending = {
      plan_status: 'pending_approval',
      plan_version: 3,
      plan_text: null,
      proposed_plan: '## Proposed steps\n\n1. Open the report page',
      plan_feedback: null,
      plan_error: null,
      current_planning_task_id: null,
      planning_task_history: [],
    }

    it('shows the proposed plan expanded, with Approve and Request changes', async () => {
      mockGet.mockResolvedValue(pending)
      renderPanel(sched({ plan_status: 'pending_approval', plan_version: 3 }))
      const md = await screen.findByTestId('plan-proposed-markdown')
      expect(md.textContent).toContain('Proposed steps')
      expect(md.textContent).toContain('Open the report page')
      expect(screen.getByTestId('schedule-plan-status').textContent).toContain(
        'Waiting for your approval',
      )
      expect(screen.getByText(/Nothing runs until you approve it/)).toBeTruthy()
      expect(screen.getByTestId('plan-approve').textContent).toContain('Approve')
      expect(screen.getByTestId('plan-request-changes')).toBeTruthy()
      // No approved plan yet — nothing "keeps running".
      expect(screen.queryByTestId('plan-approved-keeps-running')).toBeNull()
    })

    it('shows the changes the user asked for next to the revision', async () => {
      mockGet.mockResolvedValue({ ...pending, plan_feedback: 'Skip weekends' })
      renderPanel(sched({ plan_status: 'pending_approval', plan_version: 3 }))
      const shown = await screen.findByTestId('plan-feedback-shown')
      expect(shown.textContent).toContain('Changes you asked for:')
      expect(shown.textContent).toContain('Skip weekends')
    })

    it('Approve posts the plan_version it read and hands back the schedule', async () => {
      mockGet.mockResolvedValue(pending)
      const updated = sched({ plan_status: 'ready', plan_version: 3, plan: 'x' })
      mockPost.mockResolvedValue(updated)
      const onUpdated = vi.fn()
      // The schedule row is behind the /plan response (version 2):
      // the version sent must be the one the user is looking at.
      renderPanel(sched({ plan_status: 'pending_approval', plan_version: 2 }), { onUpdated })
      await screen.findByTestId('plan-proposed-markdown')
      fireEvent.click(screen.getByTestId('plan-approve'))
      await waitFor(() => expect(mockPost).toHaveBeenCalledWith(
        '/api/schedules/sched-1/plan/approve',
        { plan_version: 3 },
      ))
      await waitFor(() => expect(onUpdated).toHaveBeenCalledWith(updated))
    })

    it('shows the server reason when approval is refused', async () => {
      mockGet.mockResolvedValue(pending)
      mockPost.mockRejectedValue(new Error('The plan changed since you opened it.'))
      renderPanel(sched({ plan_status: 'pending_approval', plan_version: 3 }))
      await screen.findByTestId('plan-proposed-markdown')
      fireEvent.click(screen.getByTestId('plan-approve'))
      const err = await screen.findByTestId('plan-action-error')
      expect(err.textContent).toContain('The plan changed since you opened it.')
    })

    it('Request changes sends the feedback with plan_version', async () => {
      mockGet.mockResolvedValue(pending)
      mockPost.mockResolvedValue(sched({ plan_status: 'planning', plan_version: 3 }))
      renderPanel(sched({ plan_status: 'pending_approval', plan_version: 3 }))
      await screen.findByTestId('plan-proposed-markdown')
      expect(screen.queryByTestId('plan-feedback-input')).toBeNull()
      fireEvent.click(screen.getByTestId('plan-request-changes'))
      const input = await screen.findByTestId('plan-feedback-input')
      fireEvent.change(input, { target: { value: 'Also check the returns page' } })
      fireEvent.click(screen.getByTestId('plan-feedback-send'))
      await waitFor(() => expect(mockPost).toHaveBeenCalledWith(
        '/api/schedules/sched-1/plan/request-changes',
        { feedback: 'Also check the returns page', plan_version: 3 },
      ))
      await waitFor(() => expect(screen.queryByTestId('plan-feedback-input')).toBeNull())
    })

    it('says the approved plan keeps running while a new one waits', async () => {
      mockGet.mockResolvedValue({ ...pending, plan_text: '## Approved steps' })
      renderPanel(sched({ plan_status: 'pending_approval', plan: '## Approved steps' }))
      await screen.findByTestId('plan-approved-keeps-running')
      // The approved plan is there too, behind its toggle.
      fireEvent.click(screen.getByTestId('plan-toggle'))
      const md = await screen.findByTestId('plan-markdown')
      expect(md.textContent).toContain('Approved steps')
    })
  })

  it('ready: Request changes is offered next to Re-plan', async () => {
    mockGet.mockResolvedValue({
      plan_status: 'ready',
      plan_version: 4,
      plan_text: '## plan',
      plan_error: null,
      current_planning_task_id: null,
      planning_task_history: [],
    })
    mockPost.mockResolvedValue(sched({ plan_status: 'planning' }))
    renderPanel(sched({ plan_status: 'ready', plan_version: 4, plan: '## plan' }))
    fireEvent.click(await screen.findByTestId('plan-request-changes'))
    fireEvent.change(await screen.findByTestId('plan-feedback-input'), {
      target: { value: 'Skip weekends' },
    })
    fireEvent.click(screen.getByTestId('plan-feedback-send'))
    await waitFor(() => expect(mockPost).toHaveBeenCalledWith(
      '/api/schedules/sched-1/plan/request-changes',
      { feedback: 'Skip weekends', plan_version: 4 },
    ))
  })

  it('failed: the retry button says Try again', async () => {
    mockGet.mockResolvedValue({
      plan_status: 'failed',
      plan_version: 0,
      plan_text: null,
      plan_error: 'boom',
      current_planning_task_id: null,
      planning_task_history: [],
    })
    renderPanel(sched({ plan_status: 'failed', plan_error: 'boom' }))
    expect((await screen.findByTestId('plan-replan')).textContent).toContain('Try again')
  })
})
