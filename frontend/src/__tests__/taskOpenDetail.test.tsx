/**
 * Opening a task: its transcript either arrives or the open fails
 * visibly — never an empty conversation that looks complete.
 *
 * Observed (review): the transcript (unpaginated) and steps fetches used
 * the 30 s default deadline and `.catch(() => [])`. On a long task or a
 * slow link the task opened with an EMPTY conversation and no error, and
 * a failed pending-question fetch silently dropped the question.
 */
import { describe, it, expect, vi, afterEach } from 'vitest'
import { act, fireEvent, screen } from '@testing-library/react'
import { bootApp } from '../test/appHarness'
import { DEFAULT_TIMEOUT_MS } from '../lib/requestDeadline'

vi.mock('../lib/telemetry', () => ({
  sendEvent: vi.fn(), lengthBucket: () => 'small', ageBucket: () => 'new',
  registerUserHash: vi.fn(), unregisterUserHash: vi.fn(),
}))

const TRANSCRIPT = [
  { role: 'user', content: 'Review the example listings' },
  { role: 'assistant', content: 'Checked widget-001' },
]
const ok = (body: unknown) => ({ ok: true, status: 200, json: async () => body })
const isMessages = (url: string, init?: RequestInit) =>
  url === '/api/tasks/task-1/messages' && (init?.method ?? 'GET') === 'GET'

describe('opening a task with a slow or failing transcript', () => {
  afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals() })

  it('a transcript slower than the default deadline still arrives', async () => {
    window.history.pushState({}, '', '/tasks/task-1')
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'], shouldAdvanceTime: true })
    await bootApp({
      route: (url, init) => isMessages(url, init)
        ? new Promise((resolve, reject) => {
          // Like real fetch: an abort rejects the pending request.
          init?.signal?.addEventListener('abort', () =>
            reject(new DOMException('aborted', 'AbortError')))
          setTimeout(() => resolve(ok(TRANSCRIPT)), DEFAULT_TIMEOUT_MS + 10_000)
        })
        : undefined,
    })
    await act(async () => { await vi.advanceTimersByTimeAsync(DEFAULT_TIMEOUT_MS + 10_000) })

    expect(screen.getByText('Checked widget-001')).toBeInTheDocument()
    expect(screen.queryByTestId('task-open-error')).toBeNull()
  })

  it('a failed transcript is an error with Try again, not an empty task', async () => {
    window.history.pushState({}, '', '/tasks/task-1')
    let fail = true
    await bootApp({
      messages: TRANSCRIPT,
      route: (url, init) => isMessages(url, init) && fail
        ? Promise.resolve({ ok: false, status: 500, json: async () => ({ detail: 'transcript unavailable' }) })
        : undefined,
    })

    expect(screen.getByTestId('task-open-error')).toHaveTextContent(/transcript unavailable/i)

    fail = false
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /try again/i })) })
    expect(await screen.findByText('Checked widget-001')).toBeInTheDocument()
    expect(screen.queryByTestId('task-open-error')).toBeNull()
  })

  it('a failed pending-question fetch fails the open instead of dropping the question', async () => {
    window.history.pushState({}, '', '/tasks/task-1')
    await bootApp({
      task: { status: 'running' },
      route: url => url === '/api/tasks/task-1/questions/pending'
        ? Promise.reject(new TypeError('Failed to fetch'))
        : undefined,
    })
    expect(screen.getByTestId('task-open-error')).toHaveTextContent(/Failed to fetch/i)
  })
})
