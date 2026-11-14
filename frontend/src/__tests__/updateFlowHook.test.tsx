/**
 * useUpdateFlow: which state pops the modal, what dismissal does for
 * the rest of the browser session, and that a restart request moves
 * the UI onto the waiting overlay (and reloads when the server comes
 * back on the new version).
 *
 * Two things the hook must NOT decide for itself: whether a ready
 * update is worth interrupting someone for (that is `popup_due`, which
 * the server times against idleness, not the page), and whether the
 * range an update just crossed has already been shown (a reload is the
 * end of that update, and every tab opened later sees the same record,
 * so the "once" has to live in localStorage).
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { useUpdateFlow } from '../hooks/useUpdateFlow'
import { GUIDE_SEEN_KEY } from '../hooks/useOnboardingGuide'
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
  // Installer path by default — the one #244 built, which asks as soon
  // as it is staged. The pip cases override.
  path: 'installer',
  auto_apply: false,
  popup_due: true,
  ready_since: 1765670400,
  last_update: null,
  refusal: null,
  ...over,
})

const PIP = (over: Record<string, unknown> = {}) =>
  STATUS({ path: 'pip', auto_apply: true, popup_due: false, ...over })

const LAST_OK = {
  status: 'ok',
  from: '0.1.0',
  to: '0.3.2',
  at: '2026-11-13T00:00:00Z',
  path: 'pip',
  trigger: 'auto_idle',
  stage: null,
  error: null,
  error_class: null,
  seconds: 11.4,
}

const RANGE = {
  since: '0.1.0',
  until: '0.3.2',
  releases: ['0.3.2', '0.2.0'],
  highlights: [{ version: '0.3.2', zh: '- 亮点', en: '- Highlight' }],
  notes: [{ version: '0.2.0', text: '- 须知' }],
}

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
  localStorage.removeItem('vibe-seller:update-applied-shown')
  // Steady state the suite assumes: the first-run guide is already
  // closed, so an update is allowed to show. Guide-blocking is tested
  // where it is tested — by clearing this — and that test cannot be
  // the reason a later one sees the guide or not.
  localStorage.setItem(GUIDE_SEEN_KEY, '2026-01-01T00:00:00+00:00')
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

// ── Review round 1 ────────────────────────────────────────────────
describe('useUpdateFlow — review round 1', () => {
  it('does not auto-pop over the first-run guide; pops once it is seen', async () => {
    localStorage.removeItem(GUIDE_SEEN_KEY)
    mockRoutes({
      '/api/system/update-check': CHECK(),
      '/api/system/update-status': STATUS({ state: 'ready' }),
    })
    const { result } = renderHook(() => useUpdateFlow(user))
    await act(async () => {
      await vi.advanceTimersByTimeAsync(50)
    })
    // The guide owns the fresh-install screen; the update waits.
    expect(result.current.open).toBe(false)
    localStorage.setItem(GUIDE_SEEN_KEY, new Date().toISOString())
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1200)
    })
    expect(result.current.open).toBe(true)
    localStorage.removeItem(GUIDE_SEEN_KEY)
  })

  it('a failed apply drops the overlay and shows the error', async () => {
    let statusState = 'ready'
    mockRoutes({
      '/api/system/update-check': CHECK(),
      '/api/system/update-status': () => STATUS({ state: statusState }),
      '/api/system/update/restart': {},
    })
    const { result } = renderHook(() => useUpdateFlow(user))
    await act(async () => {
      await vi.advanceTimersByTimeAsync(50)
    })
    await act(async () => {
      await result.current.restart()
    })
    expect(result.current.status?.state).toBe('restarting')
    // The upgrade child fails; the server stays up reporting error.
    statusState = 'error'
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2500)
    })
    expect(result.current.status?.state).toBe('error')
    // The modal is still up (userOpen) and shows the error, not a
    // spinner.
    expect(result.current.open).toBe(true)
  })

  it('other tabs reload when the server comes back on a new version', async () => {
    let current = '0.1.0'
    mockRoutes({
      '/api/system/update-check': CHECK(),
      '/api/system/update-status': () =>
        STATUS({ state: 'idle', current_version: current }),
    })
    const reload = vi.fn()
    Object.defineProperty(window, 'location', {
      configurable: true,
      value: { ...window.location, reload },
    })
    renderHook(() => useUpdateFlow(user))
    await act(async () => {
      await vi.advanceTimersByTimeAsync(50)
    })
    expect(reload).not.toHaveBeenCalled()
    current = '0.3.2'
    await act(async () => {
      await vi.advanceTimersByTimeAsync(11_000)
    })
    expect(reload).toHaveBeenCalled()
  })
})

describe('useUpdateFlow — the two apply paths', () => {
  it('pip ready does NOT pop while the server may still go idle', async () => {
    mockRoutes({
      '/api/system/update-check': CHECK(),
      '/api/system/update-status': PIP({ state: 'ready' }),
    })
    const { result } = renderHook(() => useUpdateFlow(user))
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10)
    })
    expect(result.current.status?.state).toBe('ready')
    expect(result.current.status?.auto_apply).toBe(true)
    expect(result.current.open).toBe(false)
    // It stays closed for as long as the server keeps answering
    // `popup_due: false` — the 24 h of never-idle is the server's
    // clock, not the page's, so no amount of polling here pops it.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(120_000)
    })
    expect(result.current.open).toBe(false)
  })

  it('pip ready pops once the server says asking is due', async () => {
    mockRoutes({
      '/api/system/update-check': CHECK(),
      '/api/system/update-status': PIP({ state: 'ready', popup_due: true }),
    })
    const { result } = renderHook(() => useUpdateFlow(user))
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10)
    })
    expect(result.current.open).toBe(true)
  })

  it('installer ready pops immediately', async () => {
    mockRoutes({
      '/api/system/update-check': CHECK(),
      '/api/system/update-status': STATUS({ state: 'ready' }),
    })
    const { result } = renderHook(() => useUpdateFlow(user))
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10)
    })
    expect(result.current.open).toBe(true)
  })

  it('a dismissed pip update still opens from the chip', async () => {
    mockRoutes({
      '/api/system/update-check': CHECK(),
      '/api/system/update-status': PIP({ state: 'ready', popup_due: true }),
    })
    const { result } = renderHook(() => useUpdateFlow(user))
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10)
    })
    act(() => result.current.closeModal())
    expect(result.current.open).toBe(false)
    // popup_due decides whether to ASK; it never locks the dialog the
    // admin opens themselves.
    act(() => result.current.openModal())
    expect(result.current.open).toBe(true)
  })

  it('the pip path applies through the same restart endpoint', async () => {
    const fn = mockRoutes({
      '/api/system/update-check': CHECK(),
      '/api/system/update-status': PIP({ state: 'ready' }),
      '/api/system/update/restart': {},
      '/api/version': { version: '0.1.0' },
    })
    const { result } = renderHook(() => useUpdateFlow(user))
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10)
    })
    // Not popped, so this is the chip-click route: open, then Update now.
    expect(result.current.open).toBe(false)
    act(() => result.current.openModal())
    await act(async () => {
      await result.current.restart()
    })
    expect(
      fn.mock.calls.some(c => String(c[0]) === '/api/system/update/restart')
    ).toBe(true)
    expect(result.current.status?.state).toBe('restarting')
  })
})

describe('useUpdateFlow — the range an update crossed', () => {
  it('shows the crossed range once, and never again for that pair', async () => {
    const fn = mockRoutes({
      '/api/system/update-check': CHECK(),
      '/api/system/update-status': STATUS({
        state: 'idle',
        version: null,
        last_update: LAST_OK,
      }),
      '/api/system/release-notes': RANGE,
    })
    const { result, unmount } = renderHook(() => useUpdateFlow(user))
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10)
    })
    expect(result.current.updatedOpen).toBe(true)
    expect(result.current.updatedNotes?.until).toBe('0.3.2')
    const asked = fn.mock.calls
      .map(c => String(c[0]))
      .find(u => u.startsWith('/api/system/release-notes'))
    expect(asked).toBe('/api/system/release-notes?since=0.1.0&until=0.3.2')

    act(() => result.current.closeUpdated())
    expect(result.current.updatedOpen).toBe(false)

    // The reload the update itself caused: a NEW mount, the same
    // storage, the same last_update. Nothing to show twice.
    unmount()
    const second = renderHook(() => useUpdateFlow(user))
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000)
    })
    expect(second.result.current.updatedOpen).toBe(false)
    expect(localStorage.getItem('vibe-seller:update-applied-shown')).toBe(
      '0.1.0->0.3.2'
    )
    // And it did not even ask again: the pair is spent, not just hidden.
    expect(
      fn.mock.calls.filter(c =>
        String(c[0]).startsWith('/api/system/release-notes')
      )
    ).toHaveLength(1)
  })

  it('a tab opened later does not show the same pair again', async () => {
    // The record stays on the server until the next update, so every
    // new tab (a fresh sessionStorage) reads the same last_update.
    mockRoutes({
      '/api/system/update-check': CHECK(),
      '/api/system/update-status': STATUS({
        state: 'idle',
        version: null,
        last_update: LAST_OK,
      }),
      '/api/system/release-notes': RANGE,
    })
    const first = renderHook(() => useUpdateFlow(user))
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10)
    })
    expect(first.result.current.updatedOpen).toBe(true)
    first.unmount()

    sessionStorage.clear()
    const later = renderHook(() => useUpdateFlow(user))
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000)
    })
    expect(later.result.current.updatedOpen).toBe(false)
  })

  it('waits for the first-run guide, like the popup does', async () => {
    localStorage.removeItem(GUIDE_SEEN_KEY)
    mockRoutes({
      '/api/system/update-check': CHECK(),
      '/api/system/update-status': STATUS({
        state: 'idle',
        version: null,
        last_update: LAST_OK,
      }),
      '/api/system/release-notes': RANGE,
    })
    const { result } = renderHook(() => useUpdateFlow(user))
    await act(async () => {
      await vi.advanceTimersByTimeAsync(50)
    })
    expect(result.current.updatedOpen).toBe(false)
    localStorage.setItem(GUIDE_SEEN_KEY, new Date().toISOString())
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1200)
    })
    expect(result.current.updatedOpen).toBe(true)
    localStorage.removeItem(GUIDE_SEEN_KEY)
  })

  it('a rolled-back apply is not an upgrade: no notes panel', async () => {
    const fn = mockRoutes({
      '/api/system/update-check': CHECK(),
      '/api/system/update-status': STATUS({
        state: 'idle',
        version: null,
        last_update: { ...LAST_OK, status: 'rolled_back' },
      }),
      '/api/system/release-notes': RANGE,
    })
    const { result } = renderHook(() => useUpdateFlow(user))
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10)
    })
    expect(result.current.updatedOpen).toBe(false)
    expect(
      fn.mock.calls.some(c => String(c[0]).startsWith('/api/system/release'))
    ).toBe(false)
  })

  it('still works when the storage the pair lives in throws', async () => {
    // Private browsing / blocked site data: the panel is shown by the
    // page load's own state, and never loops on a storage error. The
    // guide flag is the one read that still answers, so the panel is
    // allowed to show at all.
    const real = window.localStorage
    Object.defineProperty(window, 'localStorage', {
      configurable: true,
      value: {
        getItem: (key: string) => {
          if (key === GUIDE_SEEN_KEY) return '2026-01-01T00:00:00+00:00'
          throw new Error('blocked')
        },
        setItem: () => {
          throw new Error('blocked')
        },
        removeItem: () => {},
      },
    })
    try {
      const fn = mockRoutes({
        '/api/system/update-check': CHECK(),
        '/api/system/update-status': () =>
          STATUS({ state: 'idle', version: null, last_update: LAST_OK }),
        '/api/system/release-notes': RANGE,
      })
      const { result } = renderHook(() => useUpdateFlow(user))
      await act(async () => {
        await vi.advanceTimersByTimeAsync(10)
      })
      expect(result.current.updatedOpen).toBe(true)
      act(() => result.current.closeUpdated())
      await act(async () => {
        await vi.advanceTimersByTimeAsync(60_000)
      })
      expect(result.current.updatedOpen).toBe(false)
      expect(
        fn.mock.calls.filter(c =>
          String(c[0]).startsWith('/api/system/release-notes')
        )
      ).toHaveLength(1)
    } finally {
      Object.defineProperty(window, 'localStorage', {
        configurable: true,
        value: real,
      })
    }
  })

  it('a ready installer update outranks the panel: one overlay at a time', async () => {
    mockRoutes({
      '/api/system/update-check': CHECK(),
      '/api/system/update-status': () =>
        STATUS({
          state: 'idle',
          version: null,
          last_update: LAST_OK,
        }),
      '/api/system/release-notes': RANGE,
    })
    const { result } = renderHook(() => useUpdateFlow(user))
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10)
    })
    expect(result.current.updatedOpen).toBe(true)
    // The admin opens the update dialog: the panel stands down until it
    // closes, instead of stacking two modals.
    act(() => result.current.openModal())
    expect(result.current.updatedOpen).toBe(false)
    expect(result.current.open).toBe(true)
    act(() => result.current.closeModal())
    expect(result.current.updatedOpen).toBe(true)
  })
})
