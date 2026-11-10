/**
 * Opening a task shows that it is loading, and says when it failed.
 *
 * Observed: loadTaskById swallowed a failed task fetch (`catch { return }`)
 * and nothing marked the load as pending, so a click on a task while the
 * server was down — or not answering — left the previous task (or the
 * empty hint) on screen, as if the click had done nothing. Now the pane
 * shows "Opening task…" at once, and an error with "Try again" when the
 * fetch fails or passes the request deadline.
 */
import { describe, it, expect, vi, afterEach } from 'vitest'
import { act, fireEvent, screen } from '@testing-library/react'
import { bootApp } from '../test/appHarness'
import { DEFAULT_TIMEOUT_MS } from '../lib/requestDeadline'

vi.mock('../lib/telemetry', () => ({
  sendEvent: vi.fn(), lengthBucket: () => 'small', ageBucket: () => 'new',
  registerUserHash: vi.fn(), unregisterUserHash: vi.fn(),
}))

const isTaskGet = (url: string) => url === '/api/tasks/task-1'

describe('opening a task', () => {
  afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals() })

  it('a server that never answers: pending at once, then a timeout error', async () => {
    window.history.pushState({}, '', '/tasks/task-1')
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'], shouldAdvanceTime: true })
    let answer = false
    await bootApp({
      route: (url, init) => isTaskGet(url) && !answer
        ? new Promise((_resolve, reject) => {
          init?.signal?.addEventListener('abort', () =>
            reject(new DOMException('aborted', 'AbortError')))
        })
        : undefined,
    })

    expect(screen.getByTestId('task-opening')).toHaveTextContent(/opening task/i)

    await act(async () => { await vi.advanceTimersByTimeAsync(DEFAULT_TIMEOUT_MS) })
    const err = screen.getByTestId('task-open-error')
    expect(err).toHaveTextContent(/did not answer in time/i)

    // The server is back: Try again opens it.
    answer = true
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /try again/i })) })
    expect(await screen.findByTestId('chat-input')).toBeInTheDocument()
    expect(screen.queryByTestId('task-open-error')).toBeNull()
  })

  it('a server that is down: an error at once', async () => {
    window.history.pushState({}, '', '/tasks/task-1')
    await bootApp({
      route: url => isTaskGet(url) ? Promise.reject(new TypeError('Failed to fetch')) : undefined,
    })
    expect(screen.getByTestId('task-open-error')).toHaveTextContent(/could not open this task: Failed to fetch/i)
  })
})
