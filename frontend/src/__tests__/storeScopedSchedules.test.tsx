/**
 * The 常规/Tasks ↔ 定时/Scheduled switch keeps the store.
 *
 * Reported: inside a store, flipping the sub-tabs jumped around — the
 * store's scope was dropped (定时 went to the global /schedules, and
 * back to 常规 landed on All Stores), while the sidebar/list silently
 * kept the old scope state. Scope and sub-tab are now two independent
 * parts of the URL (/stores/$storeId[/schedules[/$scheduleId]]): every
 * flip, click and leave stays in the browsed scope, and the store's
 * task list is not refetched on a sub-tab flip.
 */
import { describe, it, expect, vi, afterEach } from 'vitest'
import { act, fireEvent, screen, within } from '@testing-library/react'
import { bootApp } from '../test/appHarness'
import { makeSchedule, makeStore } from '../test/factories'

vi.mock('../lib/telemetry', () => ({
  sendEvent: vi.fn(), lengthBucket: () => 'small', ageBucket: () => 'new',
  registerUserHash: vi.fn(), unregisterUserHash: vi.fn(),
}))

const STORES = [
  makeStore({ id: 'store-a', name: 'Demo A' }),
  makeStore({ id: 'store-b', name: 'Demo B' }),
]
const SCHEDULE = makeSchedule({ id: 'sch-1', store_id: 'store-a', title: 'Weekly sync' })
const route = (url: string) =>
  url === '/api/stores' ? STORES : url === '/api/schedules' ? [SCHEDULE] : undefined
const taskFetches = (fetchMock: ReturnType<typeof vi.fn>, storeId: string) =>
  fetchMock.mock.calls.filter(([u]) => String(u).includes(`store_id=${storeId}`)).length

async function click(name: RegExp) {
  await act(async () => { fireEvent.click(screen.getByRole('button', { name })) })
}
// The sidebar nav switcher also renders a 'Tasks' button; the sub-tab
// pair is the control that also holds 'Scheduled'.
const subTab = (name: RegExp) => within(
  screen.getByRole('button', { name: /^scheduled$/i }).parentElement as HTMLElement,
).getByRole('button', { name })
async function clickSubTab(name: RegExp) {
  await act(async () => { fireEvent.click(subTab(name)) })
}

describe('sub-tab switches keep the store', () => {
  afterEach(() => { vi.unstubAllGlobals() })

  it('inside a store, Scheduled/Tasks flip in place', async () => {
    window.history.pushState({}, '', '/stores/store-a')
    const { router, fetchMock } = await bootApp({ route })
    await clickSubTab(/^scheduled$/i)
    expect(router.state.location.pathname).toBe('/stores/store-a/schedules')
    expect(screen.getByRole('button', { name: /demo a/i }).className).toContain('bg-indigo-50')
    expect(screen.getByRole('heading', { name: 'Demo A' })).toBeInTheDocument()
    // A sub-tab flip must NOT reload the store's task list.
    expect(taskFetches(fetchMock, 'store-a')).toBe(1)
    await clickSubTab(/^tasks$/i)
    expect(router.state.location.pathname).toBe('/stores/store-a')
    expect(taskFetches(fetchMock, 'store-a')).toBe(1)
  })

  it('opening a schedule keeps the store in the URL and opens the detail', async () => {
    window.history.pushState({}, '', '/stores/store-a/schedules')
    const { router } = await bootApp({ route })
    await click(/weekly sync/i)
    expect(router.state.location.pathname).toBe('/stores/store-a/schedules/sch-1')
    expect(screen.getByRole('button', { name: /run now/i })).toBeInTheDocument()
  })

  it('deleting the open schedule leaves to the store scope list, not the global one', async () => {
    window.history.pushState({}, '', '/stores/store-a/schedules/sch-1')
    const { router } = await bootApp({ route })
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    expect(screen.getByRole('button', { name: /run now/i })).toBeInTheDocument()
    await click(/^delete$/i)
    await act(async () => { await new Promise(r => setTimeout(r, 0)) })
    expect(router.state.location.pathname).toBe('/stores/store-a/schedules')
    expect(screen.queryByRole('button', { name: /run now/i })).toBeNull()
    vi.restoreAllMocks()
  })

  it('on Scheduled, sidebar clicks keep the sub-tab (store B, then All Stores)', async () => {
    window.history.pushState({}, '', '/stores/store-a/schedules')
    const { router } = await bootApp({ route })
    await click(/demo b/i)
    expect(router.state.location.pathname).toBe('/stores/store-b/schedules')
    await click(/all stores/i)
    expect(router.state.location.pathname).toBe('/schedules')
  })

  it('deep-link /stores/store-a/schedules: store selected, Scheduled active', async () => {
    window.history.pushState({}, '', '/stores/store-a/schedules')
    const { router } = await bootApp({ route })
    expect(router.state.location.pathname).toBe('/stores/store-a/schedules')
    expect(screen.getByRole('button', { name: /demo a/i }).className).toContain('bg-indigo-50')
    expect(screen.getByText('Weekly sync')).toBeInTheDocument()
    expect(subTab(/^tasks$/i).className).not.toContain('bg-white')
    expect(subTab(/^scheduled$/i).className).toContain('bg-white')
  })
})
