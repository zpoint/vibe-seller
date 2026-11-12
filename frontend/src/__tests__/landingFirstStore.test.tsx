/**
 * The homepage opens on the FIRST STORE, not on All Stores.
 *
 * '/' used to redirect straight to /tasks; with stores configured that
 * misled users into creating store-less tasks. '/' now stays put until
 * the store list arrives and only then resolves (lib/landing.ts) — and
 * it never overrides a URL a click or a deep link already chose. The
 * old (pre-#94) jump keyed on a userActedRef flag and lost exactly
 * that race: a click during login bounced back to a store. These tests
 * pin the jump, the replace semantics and the race.
 */
import { describe, it, expect, vi, afterEach } from 'vitest'
import { act, fireEvent, screen } from '@testing-library/react'
import { bootApp } from '../test/appHarness'
import { makeStore } from '../test/factories'
import { resolveLanding } from '../lib/landing'

vi.mock('../lib/telemetry', () => ({
  sendEvent: vi.fn(), lengthBucket: () => 'small', ageBucket: () => 'new',
  registerUserHash: vi.fn(), unregisterUserHash: vi.fn(),
}))

const STORES = [
  makeStore({ id: 'store-a', name: 'Demo A' }),
  makeStore({ id: 'store-b', name: 'Demo B' }),
]
const ok = (body: unknown) => ({ ok: true, status: 200, json: async () => body })
const withStores = (url: string) => (url === '/api/stores' ? STORES : undefined)
const noStores = (url: string) => (url === '/api/stores' ? [] : undefined)

describe('landing on "/"', () => {
  afterEach(() => { vi.unstubAllGlobals() })

  it('opens "/" on the first store, never loading the All Stores list', async () => {
    window.history.pushState({}, '', '/')
    const { router, fetchMock } = await bootApp({ route: withStores })
    expect(router.state.location.pathname).toBe('/stores/store-a')
    const crossStoreFetches = fetchMock.mock.calls
      .filter(([u]) => String(u).includes('store_id=__none__')).length
    expect(crossStoreFetches).toBe(0)
    // Sidebar: store A carries the selection highlight, All Stores not.
    expect(screen.getByRole('button', { name: /demo a/i }).className)
      .toContain('bg-indigo-50')
    expect(screen.getByRole('button', { name: /all stores/i }).className)
      .not.toContain('bg-indigo-50')
  })

  it('replaces "/" — Back does not return to it and bounce again', async () => {
    window.history.pushState({}, '', '/')
    let release: () => void = () => {}
    const { router } = await bootApp({
      route: url => (url === '/api/stores'
        ? new Promise(res => { release = () => res(ok(STORES)) })
        : undefined),
    })
    // Unresolved: still on '/' and the All Stores list has not loaded.
    expect(router.state.location.pathname).toBe('/')
    const lenBefore = window.history.length
    await act(async () => { release(); await new Promise(r => setTimeout(r, 0)) })
    expect(router.state.location.pathname).toBe('/stores/store-a')
    expect(window.history.length).toBe(lenBefore)
  })

  it('opens "/" on /tasks when there are no stores', async () => {
    window.history.pushState({}, '', '/')
    const { router, fetchMock } = await bootApp({ route: noStores })
    expect(router.state.location.pathname).toBe('/tasks')
    expect(fetchMock.mock.calls.some(([u]) => String(u).includes('store_id=__none__'))).toBe(true)
  })

  it('a deep link to /tasks stays on /tasks even with stores', async () => {
    window.history.pushState({}, '', '/tasks')
    const { router } = await bootApp({ route: withStores })
    expect(router.state.location.pathname).toBe('/tasks')
  })

  it('THE RACE: All Stores clicked before stores arrive is never bounced back', async () => {
    window.history.pushState({}, '', '/')
    let release: () => void = () => {}
    const { router } = await bootApp({
      route: url => (url === '/api/stores'
        ? new Promise(res => { release = () => res(ok(STORES)) })
        : undefined),
    })
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /all stores/i })) })
    expect(router.state.location.pathname).toBe('/tasks')
    await act(async () => { release(); await new Promise(r => setTimeout(r, 0)) })
    // The late store list must not override the explicit nav.
    expect(router.state.location.pathname).toBe('/tasks')
  })

  it('a deep link to a settings tab stays there with stores present', async () => {
    window.history.pushState({}, '', '/settings/stores')
    const { router } = await bootApp({ route: withStores })
    expect(router.state.location.pathname).toBe('/settings/stores')
  })

  it('logout then login lands on the first store, not All Stores', async () => {
    // Logout is not a chosen destination: it returns to '/', so the next
    // login resolves the landing like a fresh open (handleLogout used to
    // navigate to /tasks, re-creating the misleading All Stores landing).
    window.history.pushState({}, '', '/')
    const USER = { id: 'user-1', username: 'admin', role: 'admin' }
    const { router } = await bootApp({
      route: url =>
        url === '/api/stores' ? STORES
          : url === '/api/auth/status' ? { auth_required: true }
            : url === '/api/auth/login' ? USER
              : undefined,
    })
    expect(router.state.location.pathname).toBe('/stores/store-a')
    await act(async () => { fireEvent.click(screen.getByTitle('Sign Out')) })
    fireEvent.change(screen.getByPlaceholderText('Username or email'), { target: { value: 'admin' } })
    fireEvent.change(screen.getByPlaceholderText('Enter your password'), { target: { value: 'pw' } })
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /sign in/i })) })
    for (let i = 0; i < 5; i++) await act(async () => { await new Promise(r => setTimeout(r, 0)) })
    expect(router.state.location.pathname).toBe('/stores/store-a')
    // Not just the URL: the screen must agree — the store is selected
    // (the loadedScopeRef guard must not skip the re-selection after the
    // session was dropped, or the header stays on All Stores).
    expect(screen.getByRole('button', { name: /demo a/i }).className)
      .toContain('bg-indigo-50')
    expect(screen.getByRole('heading', { name: 'Demo A' })).toBeInTheDocument()
  })
})

describe('resolveLanding (live-URL guard)', () => {
  it('jumps to the first store only while the live URL is still "/"', async () => {
    window.history.replaceState({}, '', '/')
    const jump = vi.fn()
    resolveLanding(Promise.resolve(STORES), jump)
    await act(async () => { await new Promise(r => setTimeout(r, 0)) })
    expect(jump).toHaveBeenCalledWith(STORES[0])

    window.history.replaceState({}, '', '/tasks')
    const noJump = vi.fn()
    resolveLanding(Promise.resolve(STORES), noJump)
    await act(async () => { await new Promise(r => setTimeout(r, 0)) })
    expect(noJump).not.toHaveBeenCalled()
    window.history.replaceState({}, '', '/')
  })

  it('a failed store list lands on /tasks (jump(null)), and never throws', async () => {
    window.history.replaceState({}, '', '/')
    const jump = vi.fn()
    resolveLanding(Promise.reject(new Error('offline')), jump)
    await act(async () => { await new Promise(r => setTimeout(r, 0)) })
    expect(jump).toHaveBeenCalledWith(null)
  })
})
