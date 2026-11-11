import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

const sendEvent = vi.fn()
vi.mock('../lib/telemetry', async (orig) => ({
  ...(await orig<typeof import('../lib/telemetry')>()),
  sendEvent: (...args: unknown[]) => sendEvent(...args),
}))

import { api } from '../api'
import { DEFAULT_TIMEOUT_MS } from '../lib/requestDeadline'

function hangingFetch() {
  return vi.fn((_url: string, init?: RequestInit) =>
    new Promise<Response>((_resolve, reject) => {
      init?.signal?.addEventListener('abort', () =>
        reject(new DOMException('aborted', 'AbortError')))
    }))
}

describe('api_request_failed', () => {
  beforeEach(() => { vi.useFakeTimers(); sendEvent.mockClear() })
  afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals() })

  it('a timed-out request is reported with a normalized path', async () => {
    vi.stubGlobal('fetch', hangingFetch())
    const p = api.post(
      '/api/tasks/123e4567-e89b-12d3-a456-426614174000/messages?x=1',
      { content: 'hi' },
    ).catch(e => e)
    await vi.advanceTimersByTimeAsync(DEFAULT_TIMEOUT_MS)
    await p
    expect(sendEvent).toHaveBeenCalledWith('api_request_failed', {
      path: '/api/tasks/[id]/messages',
      method: 'POST',
      kind: 'timeout',
      timeout_ms: DEFAULT_TIMEOUT_MS,
    })
  })

  it('a 4xx is not reported', async () => {
    vi.stubGlobal('fetch', vi.fn(async () =>
      new Response(JSON.stringify({ detail: 'bad' }), { status: 400 })))
    await api.get('/api/tasks').catch(() => {})
    expect(sendEvent).not.toHaveBeenCalled()
  })
})
