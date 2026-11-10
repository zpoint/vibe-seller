/**
 * Every `api` call has a deadline.
 *
 * Observed: against a server that accepts the connection but never
 * answers, `fetch` stays pending forever — and so does every control
 * awaiting it (the send box kept its "sending" guard set, so every later
 * send was silently dropped). The user's bar: whether or not the backend
 * is up, a click must error fast or show it is waiting, never hang.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { api } from '../api'
import {
  DEFAULT_TIMEOUT_MS, LONG_TIMEOUT_MS, isRequestTimeout,
} from '../lib/requestDeadline'

// A server that never answers: the promise settles only when aborted.
function hangingFetch() {
  return vi.fn((_url: string, init?: RequestInit) =>
    new Promise<Response>((_resolve, reject) => {
      init?.signal?.addEventListener('abort', () =>
        reject(new DOMException('aborted', 'AbortError')))
    }))
}

describe('api request deadline', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals() })

  it.each([
    ['get', () => api.get('/api/tasks/t1')],
    ['post', () => api.post('/api/tasks/t1/messages', { content: 'hi' })],
    ['put', () => api.put('/api/settings', {})],
    ['patch', () => api.patch('/api/tasks/t1/review-plan', {})],
    ['del', () => api.del('/api/tasks/t1')],
  ])('%s rejects with a timeout error once the deadline passes', async (_m, call) => {
    vi.stubGlobal('fetch', hangingFetch())
    const p = call()
    const settled = vi.fn()
    p.then(settled, settled)

    await vi.advanceTimersByTimeAsync(DEFAULT_TIMEOUT_MS - 1)
    expect(settled).not.toHaveBeenCalled()

    await vi.advanceTimersByTimeAsync(1)
    const err = await p.catch(e => e)
    expect(isRequestTimeout(err)).toBe(true)
  })

  it('a slow-on-purpose call can ask for a longer deadline', async () => {
    vi.stubGlobal('fetch', hangingFetch())
    const p = api.post('/api/sync/knowledge/run', undefined, { timeoutMs: LONG_TIMEOUT_MS })
    const settled = vi.fn()
    p.then(settled, settled)
    await vi.advanceTimersByTimeAsync(DEFAULT_TIMEOUT_MS + 1)
    expect(settled).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(LONG_TIMEOUT_MS)
    expect(isRequestTimeout(await p.catch(e => e))).toBe(true)
  })

  it('a refused connection still fails at once, not as a timeout', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('Failed to fetch') }))
    const err = await api.get('/api/tasks').catch(e => e)
    expect(err).toBeInstanceOf(TypeError)
    expect(isRequestTimeout(err)).toBe(false)
  })
})
