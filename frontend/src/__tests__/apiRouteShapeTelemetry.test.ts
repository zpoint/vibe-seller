/**
 * api_request_failed reports a route SHAPE, never a name.
 *
 * Observed (review): the reported path only masked UUIDs, so a failing
 * call to `/api/profiles/<id>` — profile ids are often a provider or
 * model name — or to a custom skill's slug sent that name to telemetry.
 * Only the API's own static words survive now; anything else is [id].
 */
import { describe, it, expect, vi, afterEach } from 'vitest'

const sendEvent = vi.fn()
vi.mock('../lib/telemetry', async (orig) => ({
  ...(await orig<typeof import('../lib/telemetry')>()),
  sendEvent: (...args: unknown[]) => sendEvent(...args),
}))

import { api } from '../api'

describe('api_request_failed path', () => {
  afterEach(() => { vi.unstubAllGlobals(); sendEvent.mockClear() })

  it.each([
    ['/api/profiles/acme-llm-provider/set-default', '/api/profiles/[id]/set-default'],
    ['/api/workspace/skills/acme-private-skill', '/api/workspace/skills/[id]'],
    ['/api/email-accounts/someone@example.com', '/api/email-accounts/[id]'],
    ['/api/tasks/task-1/files', '/api/tasks/[id]/files'],
  ])('%s → %s', async (url, shape) => {
    vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('Failed to fetch') }))
    await api.get(url).catch(() => {})
    expect(sendEvent).toHaveBeenCalledWith('api_request_failed', expect.objectContaining({ path: shape }))
  })
})
