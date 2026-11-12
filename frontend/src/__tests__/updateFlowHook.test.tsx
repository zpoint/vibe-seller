/**
 * useUpdateFlow: which state pops the modal, what dismissal does for
 * the rest of the browser session, and that a restart request moves
 * the UI onto the waiting overlay (and reloads when the server comes
 * back on the new version).
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { useUpdateFlow } from '../hooks/useUpdateFlow'
import type { AuthUser } from '../types'

const user = { id: 'u1', username: 'admin', role: 'admin' } as AuthUser

const CHECK = (over: Record<string, unknown> = {}) => ({
  dev: false,
  update_available: true,
  current_version: '0.1.0',
  latest_version: '0.3.2',
  channel: 'stable',
  platform: 'windows',
  upgrade_command: null,
  download_url: 'https://gh/releases/latest',
  releases_page_url: 'https://gh/releases',
  in_app_update: true,
  releases: [],
  highlights: [],
  notes: [],
  ...over,
})

const STATUS = (over: Record<string, unknown> = {}) => ({
  supported: true,
  state: 'ready',
  version: '0.3.2',
  current_version: '0.1.0',
  downloaded_bytes: null,
  total_bytes: null,
  error: null,
  running_tasks: 0,
  platform: 'windows',
  ...over,
})

function mockRoutes(routes: Record<string, unknown>) {
  const fn = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    const key = Object.keys(routes).find(k => url.startsWith(k))
    if (!key) throw new Error(`unmocked ${url}`)
    const body = routes[key]
    if (body instanceof Error) throw body
    return {
      ok: true,
      status: 200,
      json: async () => (typeof body === 'function' ? body() : body),
    } as unknown as Response
  })
  global.fetch = fn as unknown as typeof fetch
  return fn
}

beforeEach(() => {
  sessionStorage.clear()
  vi.useFakeTimers()
})

afterEach(() => {
  vi.useRealTimers()
})

describe('useUpdateFlow', () => {
  it('ready update pops the modal on load', async () => {
    mockRoutes({
      '/api/system/update-check': CHECK(),
      '/api/system/update-status': STATUS({ state: 'ready' }),
    })
    const { result } = renderHook(() => useUpdateFlow(user))
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10)
    })
    expect(result.current.open).toBe(true)
    expect(result.current.status?.state).toBe('ready')
  })

  it('downloading never pops the modal', async () => {
    mockRoutes({
      '/api/system/update-check': CHECK(),
      '/api/system/update-status': STATUS({
        state: 'downloading',
        downloaded_bytes: 100,
        total_bytes: 1000,
      }),
    })
    const { result } = renderHook(() => useUpdateFlow(user))
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10)
    })
    expect(result.current.open).toBe(false)
  })

  it('稍后 hides it for the browser session; a new session pops again', async () => {
    mockRoutes({
      '/api/system/update-check': CHECK(),
      '/api/system/update-status': STATUS({ state: 'ready' }),
    })
    const { result } = renderHook(() => useUpdateFlow(user))
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10)
    })
    expect(result.current.open).toBe(true)
    act(() => result.current.closeModal())
    expect(result.current.open).toBe(false)
    // The poll keeps answering "ready" — it must not re-pop.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(15_000)
    })
    expect(result.current.open).toBe(false)
    expect(sessionStorage.getItem('vibe-seller:update-dismissed-version')).toBe(
      '0.3.2'
    )

    // New browser session: sessionStorage cleared, mount again.
    sessionStorage.clear()
    const { result: again } = renderHook(() => useUpdateFlow(user))
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10)
    })
    expect(again.current.open).toBe(true)
  })

  it('the sidebar chip re-opens a dismissed update', async () => {
    mockRoutes({
      '/api/system/update-check': CHECK(),
      '/api/system/update-status': STATUS({ state: 'ready' }),
    })
    const { result } = renderHook(() => useUpdateFlow(user))
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10)
    })
    act(() => result.current.closeModal())
    act(() => result.current.openModal())
    expect(result.current.open).toBe(true)
  })

  it('manual install: popup on load from update-check alone', async () => {
    mockRoutes({
      '/api/system/update-check': CHECK({ in_app_update: false }),
      '/api/system/update-status': STATUS({ supported: false, state: 'idle' }),
    })
    const { result } = renderHook(() => useUpdateFlow(user))
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10)
    })
    expect(result.current.open).toBe(true)
    act(() => result.current.closeModal())
    await act(async () => {
      await vi.advanceTimersByTimeAsync(15_000)
    })
    expect(result.current.open).toBe(false)
  })

  it('restart: overlay + version polling + reload when the version moved', async () => {
    let version = '0.1.0'
    const fn = mockRoutes({
      '/api/system/update-check': CHECK(),
      '/api/system/update-status': STATUS({ state: 'ready' }),
      '/api/system/update/restart': {},
      '/api/version': () => ({ version, pending_version: null }),
    })
    const reload = vi.fn()
    Object.defineProperty(window, 'location', {
      configurable: true,
      value: { ...window.location, reload },
    })
    const { result } = renderHook(() => useUpdateFlow(user))
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10)
    })
    expect(result.current.open).toBe(true)
    await act(async () => {
      await result.current.restart()
    })
    const post = fn.mock.calls.find(c => String(c[0]).includes('/restart'))
    expect(post).toBeTruthy()
    expect(result.current.status?.state).toBe('restarting')
    expect(result.current.open).toBe(true)
    // Server comes back on the new version → the page reloads itself.
    version = '0.3.2'
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2500)
    })
    expect(reload).toHaveBeenCalled()
  })

  it('restart failure (409) surfaces inline, no overlay', async () => {
    mockRoutes({
      '/api/system/update-check': CHECK(),
      '/api/system/update-status': STATUS({ state: 'ready' }),
    })
    global.fetch = vi.fn(async (url: string) => {
      if (String(url).includes('/restart')) {
        return {
          ok: false,
          status: 409,
          json: async () => ({ detail: { message: 'not ready' } }),
        } as unknown as Response
      }
      if (String(url).includes('/update-status')) {
        return { ok: true, json: async () => STATUS({ state: 'ready' }) } as unknown as Response
      }
      return { ok: true, json: async () => CHECK() } as unknown as Response
    }) as never
    const { result } = renderHook(() => useUpdateFlow(user))
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10)
    })
    await act(async () => {
      await result.current.restart()
    })
    expect(result.current.restartError).toBe('not ready')
    expect(result.current.status?.state).toBe('ready')
  })
})
