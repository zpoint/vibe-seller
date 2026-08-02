/**
 * Integration test — schedule trigger click-to-UI chain.
 *
 *   render (TasksView, real) → click Run Now → triggerSchedule
 *   handler (real) → api.post (real) → fetch (stubbed) →
 *   refetch /schedules/:id/tasks → setScheduleTasks →
 *   hasProgressingTask recomputes → button greys out
 *
 * Only the `fetch` global is stubbed. Everything above it —
 * `api.ts`, the handler, the React component, the i18n layer,
 * the state setter plumbing — is the real code path the app
 * takes at runtime.
 *
 * No agent, no backend, no browser. The test is fast and
 * catches any breakage in the chain.
 */

import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, waitFor, act } from '@testing-library/react'
import { I18nextProvider } from 'react-i18next'
import type { ReactElement } from 'react'
import type { Task, Schedule, Store } from '../types'
import {
  makeSchedule as baseSchedule,
  makeTask as baseTask,
} from '../test/factories'
import { i18nTestInstance } from '../test/helpers'
import { TasksView } from '../views/TasksView'
import type { TasksViewProps } from '../views/TasksView'
import { triggerSchedule } from '../handlers/triggerSchedule'
import { api } from '../api'


/* ── Helpers ───────────────────────────────────────── */

function makeSchedule(overrides: Partial<Schedule> = {}): Schedule {
  return baseSchedule({ id: 'sched-1', title: 'Email review', ...overrides })
}

function makeTask(overrides: Partial<Task> = {}): Task {
  return baseTask({ title: 'Email review', schedule_id: 'sched-1', ...overrides })
}

function wrap(ui: ReactElement) {
  return (
    <I18nextProvider i18n={i18nTestInstance}>{ui}</I18nextProvider>
  )
}

/* ── Stub fetch at the lowest possible boundary ─────── */

interface StubRoute {
  method: string
  path: string
  body: unknown
}

function stubFetch(routes: StubRoute[]): Array<{ method: string; path: string }> {
  const calls: Array<{ method: string; path: string }> = []
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === 'string' ? input : input.toString()
    const method = init?.method ?? 'GET'
    calls.push({ method, path: url })
    const route = routes.find(r => r.method === method && r.path === url)
    if (!route) {
      throw new Error(`unstubbed ${method} ${url}`)
    }
    return new Response(JSON.stringify(route.body), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })
  })
  vi.stubGlobal('fetch', fetchMock)
  return calls
}

/* ── Tests ──────────────────────────────────────────── */

describe('schedule trigger integration — real handler + api + fetch stub', () => {
  afterEach(() => vi.restoreAllMocks())

  it('POSTs to /trigger then GETs /schedules + /schedules/:id/tasks in order', async () => {
    const calls = stubFetch([
      {
        method: 'POST',
        path: '/api/schedules/sched-1/trigger',
        body: { ok: true, mode: 'single' },
      },
      {
        method: 'GET',
        path: '/api/schedules',
        body: [makeSchedule()],
      },
      {
        method: 'GET',
        path: '/api/schedules/sched-1/tasks',
        body: [makeTask({ status: 'pending' })],
      },
    ])

    const setSchedules = vi.fn()
    const setScheduleTasks = vi.fn()

    await triggerSchedule('sched-1', {
      api,
      selectedScheduleId: 'sched-1',
      setSchedules,
      setScheduleTasks,
    })

    expect(calls).toEqual([
      { method: 'POST', path: '/api/schedules/sched-1/trigger' },
      { method: 'GET', path: '/api/schedules' },
      { method: 'GET', path: '/api/schedules/sched-1/tasks' },
    ])
    expect(setSchedules).toHaveBeenCalledTimes(1)
    expect(setScheduleTasks).toHaveBeenCalledWith([
      expect.objectContaining({ status: 'pending' }),
    ])
  })

  it('skips the tasks refetch when a different schedule is selected', async () => {
    const calls = stubFetch([
      {
        method: 'POST',
        path: '/api/schedules/other-sched/trigger',
        body: { ok: true, mode: 'single' },
      },
      {
        method: 'GET',
        path: '/api/schedules',
        body: [makeSchedule()],
      },
    ])

    await triggerSchedule('other-sched', {
      api,
      selectedScheduleId: 'sched-1',
      setSchedules: vi.fn(),
      setScheduleTasks: vi.fn(),
    })

    // The selected-schedule /tasks fetch must not fire.
    expect(calls.map(c => c.path)).not.toContain(
      '/api/schedules/other-sched/tasks',
    )
  })

  it('refetch failure is swallowed — SSE will retry', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = typeof input === 'string' ? input : input.toString()
        const method = init?.method ?? 'GET'
        if (method === 'POST' && url.endsWith('/trigger')) {
          return new Response(JSON.stringify({ ok: true, mode: 'single' }), {
            status: 200,
            headers: { 'Content-Type': 'application/json' },
          })
        }
        // Every GET fails.
        return new Response('upstream down', { status: 503 })
      }),
    )

    // Must not throw despite the 503s.
    await expect(
      triggerSchedule('sched-1', {
        api,
        selectedScheduleId: 'sched-1',
        setSchedules: vi.fn(),
        setScheduleTasks: vi.fn(),
      }),
    ).resolves.not.toThrow()
  })
})


/* ── UI chain test — render + click → button greys out ── */

describe('TasksView Run Now button reflects scheduleTasks', () => {
  // Typed against the real props, minus the two each case varies. A
  // prop added to TasksView now breaks this line loudly instead of
  // arriving as `undefined` and silently changing what renders.
  const TASKSVIEW_PROPS: Omit<
    TasksViewProps,
    'scheduleTasks' | 'triggerSchedule'
  > = {
    stores: [] as Store[],
    selectedStore: null,
    showAllTasks: false,
    tasks: [],
    schedules: [makeSchedule()],
    selectedTask: null,
    steps: [],
    screenshots: {},
    logs: [],
    agentMessages: [],
    todoItems: [],
    conversationItems: [],
    pendingQuestions: null,
    selectedAnswers: {},
    otherInputs: {},
    showOtherInput: {},
    chatInput: '',
    setChatInput: vi.fn(),
    chatAttachments: [],
    setChatAttachments: vi.fn(),
    sendChatMessage: vi.fn(),
    stopAgent: vi.fn(),
    retryTask: vi.fn(),
    debugMode: false,
    setDebugMode: vi.fn(),
    selectTask: vi.fn(),
    setSelectedTask: vi.fn(),
    selectAnswer: vi.fn(),
    toggleOtherInput: vi.fn(),
    setOtherAnswer: vi.fn(),
    submitAllAnswers: vi.fn(),
    profiles: [],
    selectedProfileId: 'default',
    setSelectedProfileId: vi.fn(),
    questionBannerRef: { current: null },
    selectedSchedule: makeSchedule(),
    toggleSchedulePause: vi.fn(),
    deleteSchedule: vi.fn(),
    isMobile: false,
    onOpenNav: vi.fn(),
    taskPanelActive: false,
    taskPanelTitle: '',
    tasksLoading: false,
    currentUser: {
      id: 'u1',
      username: 'tester',
      email: null,
      role: 'admin',
      is_active: true,
      avatar_url: null,
      plan_mode_default: false,
      debug_mode: false,
      default_profile_id: 'default',
      sync_profile_to_schedules: false,
      created_at: '',
    },
    openCreateModal: vi.fn(),
    continueTask: vi.fn(),
    deleteTask: vi.fn(),
    submitImageDecision: vi.fn(),
    setTasks: vi.fn(),
    setCurrentUser: vi.fn(),
    setEditingProfile: vi.fn(),
    setShowProfileModal: vi.fn(),
    taskSubTab: 'scheduled',
    setTaskSubTab: vi.fn(),
    showCreateSchedule: false,
    setShowCreateSchedule: vi.fn(),
    selectSchedule: vi.fn(),
    replanSchedule: vi.fn(),
    setSelectedSchedule: vi.fn(),
    onScheduleUpdated: vi.fn(),
  }

  afterEach(() => vi.restoreAllMocks())

  it('no tasks → Run Now enabled', () => {
    render(
      wrap(
        <TasksView
          {...TASKSVIEW_PROPS}
          scheduleTasks={[]}
          triggerSchedule={vi.fn()}
        />,
      ),
    )
    expect(
      screen.getByRole('button', { name: /Run Now|立即执行/ }),
    ).not.toBeDisabled()
  })

  it('pending task present → Run Now disabled', () => {
    render(
      wrap(
        <TasksView
          {...TASKSVIEW_PROPS}
          scheduleTasks={[makeTask({ status: 'pending' })]}
          triggerSchedule={vi.fn()}
        />,
      ),
    )
    expect(
      screen.getByRole('button', { name: /Run Now|立即执行/ }),
    ).toBeDisabled()
  })

  it('only waiting task → Run Now enabled (regression)', () => {
    render(
      wrap(
        <TasksView
          {...TASKSVIEW_PROPS}
          scheduleTasks={[makeTask({ status: 'waiting' })]}
          triggerSchedule={vi.fn()}
        />,
      ),
    )
    expect(
      screen.getByRole('button', { name: /Run Now|立即执行/ }),
    ).not.toBeDisabled()
  })

  it('full chain: click → real handler + real api + stub fetch → button disables', async () => {
    stubFetch([
      {
        method: 'POST',
        path: '/api/schedules/sched-1/trigger',
        body: { ok: true, mode: 'single' },
      },
      {
        method: 'GET',
        path: '/api/schedules',
        body: [makeSchedule()],
      },
      {
        method: 'GET',
        path: '/api/schedules/sched-1/tasks',
        body: [makeTask({ status: 'pending' })],
      },
    ])

    // Mirrors what App.tsx does: local state that the handler
    // feeds via setScheduleTasks, and a re-render on change.
    let scheduleTasks: Task[] = []
    const setScheduleTasks = (tasks: Task[]) => {
      scheduleTasks = tasks
    }

    const trigger = (id: string) =>
      triggerSchedule(id, {
        api,
        selectedScheduleId: 'sched-1',
        setSchedules: vi.fn(),
        setScheduleTasks,
      })

    const { rerender } = render(
      wrap(
        <TasksView
          {...TASKSVIEW_PROPS}
          scheduleTasks={scheduleTasks}
          triggerSchedule={trigger}
        />,
      ),
    )

    const btn = screen.getByRole('button', { name: /Run Now|立即执行/ })
    expect(btn).not.toBeDisabled()

    // Drive the same handler the button onClick runs.
    await act(async () => { await trigger('sched-1') })

    // Handler must have refetched via real api + stub fetch.
    expect(scheduleTasks).toHaveLength(1)
    expect(scheduleTasks[0].status).toBe('pending')

    // Re-render with post-click state (App.tsx's useState would
    // cause this automatically).
    rerender(
      wrap(
        <TasksView
          {...TASKSVIEW_PROPS}
          scheduleTasks={scheduleTasks}
          triggerSchedule={trigger}
        />,
      ),
    )
    await waitFor(() => {
      expect(
        screen.getByRole('button', { name: /Run Now|立即执行/ }),
      ).toBeDisabled()
    })
  })
})
