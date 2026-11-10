/**
 * Task header actions show they are in flight and say when they failed.
 *
 * Observed: Stop, Continue, Retry, the Auto/Plan switch and "run the
 * plan" each swallowed their request's failure (`catch { /* ignore *\/ }`
 * or a silent refetch). With the server down or not answering, the click
 * did visibly nothing. Now the button is marked busy while the request
 * is in flight and the failure is shown in words in the task header.
 */
import { describe, it, expect, vi, afterEach } from 'vitest'
import { act, fireEvent, screen } from '@testing-library/react'
import { bootApp } from '../test/appHarness'
import { DEFAULT_TIMEOUT_MS } from '../lib/requestDeadline'

vi.mock('../lib/telemetry', () => ({
  sendEvent: vi.fn(), lengthBucket: () => 'small', ageBucket: () => 'new',
  registerUserHash: vi.fn(), unregisterUserHash: vi.fn(),
}))

const hang = (init?: RequestInit) => new Promise((_resolve, reject) => {
  init?.signal?.addEventListener('abort', () =>
    reject(new DOMException('aborted', 'AbortError')))
})

describe('task header actions', () => {
  afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals() })

  it('Stop against a server that never answers: busy, then a timeout error', async () => {
    window.history.pushState({}, '', '/tasks/task-1')
    await bootApp({
      task: { status: 'running' },
      route: (url, init) => url === '/api/tasks/task-1/agent/stop' ? hang(init) : undefined,
    })
    const stop = await screen.findByRole('button', { name: /stop/i })
    vi.useFakeTimers()
    await act(async () => { fireEvent.click(stop) })

    expect(stop).toBeDisabled()
    expect(stop).toHaveAttribute('aria-busy', 'true')

    await act(async () => { await vi.advanceTimersByTimeAsync(DEFAULT_TIMEOUT_MS) })
    expect(screen.getByTestId('task-action-error')).toHaveTextContent(/did not answer in time/i)
    expect(stop).toBeEnabled()
  })

  it('Continue against a server that is down: an error at once', async () => {
    window.history.pushState({}, '', '/tasks/task-1')
    await bootApp({
      task: { status: 'failed', error: 'stopped' },
      route: (url, init) => url === '/api/tasks/task-1/messages' && init?.method === 'POST'
        ? Promise.reject(new TypeError('Failed to fetch'))
        : undefined,
    })
    const cont = await screen.findByRole('button', { name: /^continue$/i })
    await act(async () => { fireEvent.click(cont) })

    expect(screen.getByTestId('task-action-error')).toHaveTextContent(/did not go through: Failed to fetch/i)
  })

  it('the Auto/Plan switch: a server error is shown, the mode is unchanged', async () => {
    window.history.pushState({}, '', '/stores/store-1/tasks/task-1')
    await bootApp({
      task: { status: 'pending', store_id: 'store-1', plan_mode: false },
      route: (url, init) => {
        if (url === '/api/stores') return [{ id: 'store-1', name: 'Example store', browser_backend: 'chrome' }]
        if (url === '/api/tasks/task-1/review-plan' && init?.method === 'PATCH') {
          return Promise.resolve({ ok: false, status: 500, json: async () => ({ detail: 'database is locked' }) })
        }
        return undefined
      },
    })
    const plan = await screen.findByRole('button', { name: /^review$/i, pressed: false })
    await act(async () => { fireEvent.click(plan) })

    expect(screen.getByTestId('task-action-error')).toHaveTextContent(/did not go through: database is locked/i)
    expect(plan).toHaveAttribute('aria-pressed', 'false')
  })
})
