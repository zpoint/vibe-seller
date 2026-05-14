import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { api, AUTH_EXPIRED_EVENT } from '../api'

const mockFetch = vi.fn()
global.fetch = mockFetch

describe('api 401 → auth:expired event', () => {
  beforeEach(() => {
    mockFetch.mockClear()
  })
  afterEach(() => {
    vi.restoreAllMocks()
  })

  for (const method of ['get', 'post', 'put', 'patch', 'del'] as const) {
    it(`fires AUTH_EXPIRED_EVENT and rejects on 401 from api.${method}`, async () => {
      mockFetch.mockResolvedValueOnce({ status: 401, ok: false })
      const handler = vi.fn()
      window.addEventListener(AUTH_EXPIRED_EVENT, handler)

      const callable = method === 'get' || method === 'del'
        ? () => (api as Record<string, (u: string) => Promise<unknown>>)[method]('/x')
        : () => (api as Record<string, (u: string, b?: unknown) => Promise<unknown>>)[method]('/x', { a: 1 })

      await expect(callable()).rejects.toThrow('unauthorized')
      expect(handler).toHaveBeenCalledTimes(1)

      window.removeEventListener(AUTH_EXPIRED_EVENT, handler)
    })
  }

  it('does not fire AUTH_EXPIRED_EVENT on 200', async () => {
    mockFetch.mockResolvedValueOnce({
      status: 200,
      ok: true,
      json: () => Promise.resolve({ ok: true }),
    })
    const handler = vi.fn()
    window.addEventListener(AUTH_EXPIRED_EVENT, handler)
    await expect(api.get('/x')).resolves.toEqual({ ok: true })
    expect(handler).not.toHaveBeenCalled()
    window.removeEventListener(AUTH_EXPIRED_EVENT, handler)
  })

  it('does not fire AUTH_EXPIRED_EVENT on 500', async () => {
    mockFetch.mockResolvedValueOnce({
      status: 500,
      ok: false,
      json: () => Promise.resolve({ detail: 'oops' }),
    })
    const handler = vi.fn()
    window.addEventListener(AUTH_EXPIRED_EVENT, handler)
    await expect(api.get('/x')).rejects.toThrow('oops')
    expect(handler).not.toHaveBeenCalled()
    window.removeEventListener(AUTH_EXPIRED_EVENT, handler)
  })
})
