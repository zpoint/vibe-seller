/**
 * A task action's late response never lands on another task.
 *
 * Observed (review): Stop cleared the pending question, and the
 * Auto/Plan switch patched `selectedTask`, of whichever task was open
 * when their response ARRIVED. Click Stop (or flip the mode) on one task,
 * open another before the server answers, and the other task lost its
 * pending question (or showed the first task's mode).
 */
import { describe, it, expect, vi, afterEach } from 'vitest'
import { act, fireEvent, screen } from '@testing-library/react'
import { bootApp } from '../test/appHarness'
import { makeTask } from '../test/factories'

vi.mock('../lib/telemetry', () => ({
  sendEvent: vi.fn(), lengthBucket: () => 'small', ageBucket: () => 'new',
  registerUserHash: vi.fn(), unregisterUserHash: vi.fn(),
}))

const ok = (body: unknown) => ({ ok: true, status: 200, json: async () => body })
const STORE = { id: 'store-1', name: 'Example store', browser_backend: 'chrome' }
const QUESTION = 'Which marketplace should I check first?'

function otherTask(task: ReturnType<typeof makeTask>, pendingQuestion = false) {
  return (url: string) => {
    if (url === `/api/tasks/${task.id}`) return task
    if (url === `/api/tasks/${task.id}/messages` || url === `/api/tasks/${task.id}/steps`) return []
    if (url === `/api/tasks/${task.id}/image/pending`) return { pending: false }
    if (url === `/api/tasks/${task.id}/questions/pending`) {
      return pendingQuestion
        ? { pending: true, request_id: 'rq-1', questions: [{ question: QUESTION, options: [{ label: 'SA' }, { label: 'AE' }] }] }
        : { pending: false }
    }
    return undefined
  }
}

describe('late action responses', () => {
  afterEach(() => { vi.unstubAllGlobals() })

  it("Stop's response does not clear the next task's pending question", async () => {
    window.history.pushState({}, '', '/tasks/task-1')
    const second = makeTask({ id: 'task-2', store_id: null, status: 'running', title: 'Second task' })
    let releaseStop: () => void = () => {}
    const { router } = await bootApp({
      task: { status: 'running' },
      route: (url) => {
        if (url === '/api/tasks/task-1/agent/stop') {
          return new Promise(resolve => { releaseStop = () => resolve(ok({})) })
        }
        return otherTask(second, true)(url)
      },
    })
    await act(async () => { fireEvent.click(await screen.findByRole('button', { name: /stop/i })) })

    await act(async () => { await router.navigate({ to: '/tasks/$taskId', params: { taskId: 'task-2' } }) })
    expect(await screen.findByText(QUESTION)).toBeInTheDocument()

    await act(async () => { releaseStop() })
    expect(screen.getByText(QUESTION)).toBeInTheDocument()
  })

  it("the mode switch's response does not patch the next task", async () => {
    window.history.pushState({}, '', '/stores/store-1/tasks/task-1')
    const second = makeTask({ id: 'task-2', store_id: 'store-1', status: 'pending', plan_mode: false, title: 'Second task' })
    let releasePatch: () => void = () => {}
    const { router } = await bootApp({
      task: { status: 'pending', store_id: 'store-1', plan_mode: false },
      route: (url, init) => {
        if (url === '/api/stores') return [STORE]
        if (url === '/api/tasks/task-1/review-plan' && init?.method === 'PATCH') {
          return new Promise(resolve => { releasePatch = () => resolve(ok({})) })
        }
        return otherTask(second)(url)
      },
    })
    await act(async () => {
      fireEvent.click(await screen.findByRole('button', { name: /^review$/i, pressed: false }))
    })

    await act(async () => {
      await router.navigate({ to: '/stores/$storeId/tasks/$taskId', params: { storeId: 'store-1', taskId: 'task-2' } })
    })
    await screen.findByText('Second task', { selector: 'h2' })

    await act(async () => { releasePatch() })
    expect(screen.getByRole('button', { name: /^review$/i })).toHaveAttribute('aria-pressed', 'false')
  })
})
