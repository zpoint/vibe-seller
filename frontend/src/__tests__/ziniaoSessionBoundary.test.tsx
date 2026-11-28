import { act, renderHook } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { api } from '../api'
import {
  fetchBrowserProfiles,
  type BrowserProfilesDeps,
} from '../handlers/fetchBrowserProfiles'
import { useZiniaoAccounts } from '../hooks/useZiniaoAccounts'

vi.mock('../api', () => ({ api: { get: vi.fn() } }))

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: Error) => void
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej })
  return { promise, resolve, reject }
}

function deps(get: BrowserProfilesDeps['api']['get'], isCurrent: () => boolean) {
  return {
    api: { get },
    isCurrent,
    setFetchingBrowsers: vi.fn(),
    setZiniaoBrowsers: vi.fn(),
    setSelectedBrowserOauth: vi.fn(),
    setBrowserFetchError: vi.fn(),
    setZiniaoStartingSecs: vi.fn(),
    sleep: vi.fn(async () => {}),
  } as BrowserProfilesDeps & {
    setZiniaoBrowsers: ReturnType<typeof vi.fn>
    setBrowserFetchError: ReturnType<typeof vi.fn>
  }
}

describe('Ziniao session generation', () => {
  beforeEach(() => { vi.mocked(api.get).mockReset() })

  it('drops a late successful profile response after its session is invalidated', async () => {
    const response = deferred<Array<{ browser_name: string; browser_oauth: string }>>()
    let current = true
    const d = deps(vi.fn(() => response.promise), () => current)
    const pending = fetchBrowserProfiles('account-a', d)

    current = false
    response.resolve([{ browser_name: 'A private profile', browser_oauth: 'secret-oauth' }])
    await pending

    expect(d.setZiniaoBrowsers).not.toHaveBeenCalledWith([
      { browser_name: 'A private profile', browser_oauth: 'secret-oauth' },
    ])
  })

  it('drops a late failed profile response after its session is invalidated', async () => {
    const response = deferred<never>()
    let current = true
    const d = deps(vi.fn(() => response.promise), () => current)
    const pending = fetchBrowserProfiles('account-a', d)

    current = false
    response.reject(new Error('old session failure'))
    await pending

    expect(d.setBrowserFetchError).not.toHaveBeenCalledWith('connect_error')
  })

  it('reset clears credentials and selections and invalidates in-flight loads', async () => {
    vi.mocked(api.get).mockImplementation(url => {
      if (url === '/api/ziniao-accounts') {
        return Promise.resolve([{ id: 'account-a', name: 'Example account' }])
      }
      return new Promise(() => {})
    })
    const { result } = renderHook(() => useZiniaoAccounts())

    await act(async () => { await result.current.loadZiniaoAccounts() })
    act(() => {
      result.current.setSelectedZiniaoAccountId('account-a')
      result.current.setSelectedBrowserOauth('secret-oauth')
      result.current.setZiniaoBrowsers([{ browser_name: 'Private', browser_oauth: 'secret-oauth' }])
      result.current.setShowAddAccount(true)
      result.current.setShowAccountPassword(true)
      result.current.setEditingAccountId('account-a')
      result.current.setNewAccount({
        name: 'Example', company: 'acme', username: 'private-user', password: 'private-password',
      })
      result.current.fetchBrowserProfiles('account-a')
    })
    expect(result.current.fetchingBrowsers).toBe(true)

    act(() => { result.current.reset() })

    expect(result.current.ziniaoAccounts).toEqual([])
    expect(result.current.selectedZiniaoAccountId).toBe('')
    expect(result.current.ziniaoBrowsers).toEqual([])
    expect(result.current.selectedBrowserOauth).toBe('')
    expect(result.current.fetchingBrowsers).toBe(false)
    expect(result.current.showAddAccount).toBe(false)
    expect(result.current.showAccountPassword).toBe(false)
    expect(result.current.editingAccountId).toBe('')
    expect(result.current.newAccount).toEqual({ name: '', company: '', username: '', password: '' })
  })
})
