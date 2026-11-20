/**
 * Store setup → Ziniao store list. The user's spec: the server kills and
 * relaunches Ziniao by itself — "不要这个是否要关掉的交互". So the page
 * never offers Force Restart / Force Kill or a "close Ziniao and Refresh"
 * step; it keeps the spinner (with how long Ziniao has been starting)
 * while the server says "starting", then shows the list.
 */
import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { I18nextProvider, initReactI18next } from 'react-i18next'
import i18n from 'i18next'
import enTranslation from '../i18n/locales/en/translation.json'
import zhTranslation from '../i18n/locales/zh/translation.json'
import { fetchBrowserProfiles, type BrowserProfilesDeps } from '../handlers/fetchBrowserProfiles'
import { ZiniaoSection } from '../components/StorePickers'

const i18nTestInstance = i18n.createInstance()
i18nTestInstance.use(initReactI18next).init({
  resources: { en: { translation: enTranslation } },
  lng: 'en',
  fallbackLng: 'en',
  interpolation: { escapeValue: false },
})

function deps(get: BrowserProfilesDeps['api']['get']) {
  const d = {
    api: { get },
    setFetchingBrowsers: vi.fn(),
    setZiniaoBrowsers: vi.fn(),
    setSelectedBrowserOauth: vi.fn(),
    setBrowserFetchError: vi.fn(),
    setZiniaoStartingSecs: vi.fn(),
    sleep: vi.fn(async () => {}),
  }
  return d as unknown as BrowserProfilesDeps & typeof d
}

const starting = (secs: number) =>
  new Error(JSON.stringify({ status: 'starting', elapsed_s: secs }))

describe('fetchBrowserProfiles', () => {
  it('keeps asking while Ziniao starts, then shows the list', async () => {
    const list = [{ browser_name: 'A', browser_oauth: 'o1' }]
    const get = vi.fn()
      .mockRejectedValueOnce(starting(46))
      .mockRejectedValueOnce(starting(92))
      .mockResolvedValueOnce(list)
    const d = deps(get)
    await fetchBrowserProfiles('acct-1', d)
    expect(get).toHaveBeenCalledTimes(3)
    expect(d.setZiniaoBrowsers).toHaveBeenLastCalledWith(list)
    expect(d.setZiniaoStartingSecs).toHaveBeenCalledWith(92)
    expect(d.setBrowserFetchError).not.toHaveBeenCalledWith(expect.stringMatching(/./))
    expect(d.setFetchingBrowsers).toHaveBeenLastCalledWith(false)
  })

  it('reports a real failure with its reason', async () => {
    const get = vi.fn().mockRejectedValueOnce(
      new Error(JSON.stringify({ status: 'start_failed', message: 'opened 2 times' })),
    )
    const d = deps(get)
    await fetchBrowserProfiles('acct-1', d)
    const err = d.setBrowserFetchError.mock.calls.at(-1)?.[0] as string
    expect(err.startsWith('ziniao:start_failed:')).toBe(true)
    expect(atob(err.split(':')[2])).toBe('opened 2 times')
  })

  it('lets a newer account pick win over an older one still starting', async () => {
    let release: () => void = () => {}
    const gate = new Promise<void>(r => { release = r })
    const old = deps(vi.fn().mockRejectedValue(starting(10)))
    old.sleep = vi.fn(() => gate)
    const first = fetchBrowserProfiles('acct-old', old)
    const fresh = deps(vi.fn().mockResolvedValue([]))
    await fetchBrowserProfiles('acct-new', fresh)
    release()
    await first
    expect(old.api.get).toHaveBeenCalledTimes(1)
    expect(old.setBrowserFetchError).not.toHaveBeenCalledWith(expect.stringMatching(/./))
  })
})

function renderSection(over: Partial<Parameters<typeof ZiniaoSection>[0]>) {
  const props = {
    ziniaoAccounts: [{ id: 'acct-1', name: 'Acme', company: 'acme', username: 'ops' }],
    selectedZiniaoAccountId: 'acct-1', setSelectedZiniaoAccountId: vi.fn(),
    ziniaoBrowsers: [], selectedBrowserOauth: '', setSelectedBrowserOauth: vi.fn(),
    fetchingBrowsers: false, browserFetchError: '', setBrowserFetchError: vi.fn(),
    fetchBrowserProfiles: vi.fn(), ziniaoStartingSecs: 0, serverPlatform: 'windows',
    showAddAccount: false, setShowAddAccount: vi.fn(),
    showAccountPassword: false, setShowAccountPassword: vi.fn(),
    editingAccountId: '', setEditingAccountId: vi.fn(),
    newAccount: { name: '', company: '', username: '', password: '' }, setNewAccount: vi.fn(),
    createZiniaoAccount: vi.fn(), updateZiniaoAccount: vi.fn(), deleteZiniaoAccount: vi.fn(),
    isAdmin: false,
    ...over,
  } as Parameters<typeof ZiniaoSection>[0]
  return render(
    <I18nextProvider i18n={i18nTestInstance}>
      <ZiniaoSection {...props} />
    </I18nextProvider>,
  )
}

describe('ZiniaoSection', () => {
  it.each(['ziniao:running_normal', 'ziniao:start_failed', 'ziniao:not_running', 'connect_error'])(
    'never offers to force-restart or force-kill Ziniao (%s)',
    status => {
      for (const platform of ['windows', 'mac', 'wsl'] as const) {
        // The old picker showed these after a failed Refresh; pass its
        // retry flag too so this test would see them if they came back.
        const legacy = { ziniaoRetried: true, restartZiniao: vi.fn() } as object
        const { unmount } = renderSection({ ...legacy, browserFetchError: status, serverPlatform: platform })
        expect(screen.queryByText(/force restart|force kill/i)).toBeNull()
        unmount()
      }
    },
  )

  it('shows how long Ziniao has been starting', () => {
    renderSection({ fetchingBrowsers: true, ziniaoStartingSecs: 92 })
    expect(screen.getByText(/92s so far/)).toBeTruthy()
  })

  it('says why Ziniao did not start', () => {
    const msg = btoa('opened 2 times')
    renderSection({ browserFetchError: `ziniao:start_failed:${msg}` })
    expect(screen.getByText(enTranslation.settings.ziniaoStartFailed)).toBeTruthy()
    expect(screen.getByText('opened 2 times')).toBeTruthy()
  })

  it('has no legacy Force Restart copy left in either language', () => {
    for (const tr of [enTranslation, zhTranslation]) {
      const s = tr.settings as Record<string, string>
      for (const key of ['ziniaoForceRestart', 'ziniaoForceKill', 'ziniaoRunningNormalMode', 'ziniaoStillRunning', 'ziniaoNormalModeHint', 'ziniaoRestartFailed']) {
        expect(s[key]).toBeUndefined()
      }
    }
  })
})
