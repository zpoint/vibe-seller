/**
 * A supported install whose background download failed gets the manual
 * route this app showed before it updated itself: the installer
 * download link on Windows, `vibe-seller upgrade` on macOS/Linux.
 *
 * The server decides WHEN (`manual_due`: a failure stands and no newer
 * attempt is under way) and stops reporting it once a download succeeds
 * or the admin updated by hand; what is pinned here is that the page
 * follows that answer exactly — pops on a failure, comes back after the
 * next failed attempt even if "稍后" was clicked, stays out of the way
 * of a retry that is downloading, hands over to the normal ready popup
 * when a retry succeeds, and says nothing once the machine is current.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, renderHook, screen, fireEvent, act } from '@testing-library/react'
import { I18nextProvider, initReactI18next } from 'react-i18next'
import i18n from 'i18next'
import enTranslation from '../i18n/locales/en/translation.json'
import zhTranslation from '../i18n/locales/zh/translation.json'
import { UpdateAvailableModal } from '../components/UpdateAvailableModal'
import { UpdateChip } from '../components/UpdateChip'
import { useUpdateFlow } from '../hooks/useUpdateFlow'
import { GUIDE_SEEN_KEY } from '../hooks/useOnboardingGuide'
import type {
  AuthUser,
  DownloadFailure,
  UpdateCheckResult,
  UpdateStatus,
} from '../types'

const make = (lng: 'en' | 'zh' = 'zh') => {
  const instance = i18n.createInstance()
  instance.use(initReactI18next).init({
    lng,
    resources: {
      en: { translation: enTranslation },
      zh: { translation: zhTranslation },
    },
    interpolation: { escapeValue: false },
  })
  return instance
}

const RELEASES = 'https://github.com/zpoint/vibe-seller/releases/latest'

const failure = (over: Partial<DownloadFailure> = {}): DownloadFailure => ({
  version: '0.3.6',
  path: 'installer',
  stage: 'download',
  error: 'installer size mismatch: expected 720487430, got 41943040',
  attempts: 1,
  at: 1764118908,
  ...over,
})

/** The server's answer after a failed download: idle or error, the
 *  failure, and the route by hand for this platform. */
const failed = (over: Partial<UpdateStatus> = {}): UpdateStatus => ({
  supported: true,
  state: 'error',
  version: '0.3.6',
  current_version: '0.3.4',
  downloaded_bytes: null,
  total_bytes: null,
  error: 'installer size mismatch: expected 720487430, got 41943040',
  running_tasks: 0,
  platform: 'windows',
  path: 'installer',
  auto_apply: false,
  popup_due: false,
  ready_since: null,
  last_update: null,
  refusal: null,
  download_failure: failure(),
  manual_due: true,
  manual_update: { upgrade_command: null, download_url: RELEASES },
  ...over,
})

const check = (over: Partial<UpdateCheckResult> = {}): UpdateCheckResult => ({
  dev: false,
  update_available: true,
  current_version: '0.3.4',
  latest_version: '0.3.6',
  channel: 'stable',
  platform: 'windows',
  upgrade_command: null,
  download_url: RELEASES,
  releases_page_url: RELEASES,
  in_app_update: true,
  releases: [],
  highlights: [],
  notes: [],
  ...over,
})

// ── the dialog ───────────────────────────────────────────────────────

const renderModal = (
  lng: 'en' | 'zh',
  props: Partial<React.ComponentProps<typeof UpdateAvailableModal>> = {}
) => {
  const onClose = vi.fn()
  render(
    <I18nextProvider i18n={make(lng)}>
      <UpdateAvailableModal
        check={check()}
        status={failed()}
        isAdmin
        onClose={onClose}
        onRestart={vi.fn()}
        restartError={null}
        restartTimedOut={false}
        {...props}
      />
    </I18nextProvider>
  )
  return { onClose }
}

describe('the manual popup after a failed download', () => {
  // The same popup an install that cannot update itself gets — what is
  // new and how to update by hand — plus ONE line: it failed, why, and
  // to update by hand. Nothing else about it is new.
  it('windows: the manual popup, the download link, and the one-line hint', () => {
    const { onClose } = renderModal('zh')
    expect(screen.getByText('有可用更新')).toBeTruthy()
    expect(
      screen.getByText('当前版本 v0.3.4 已过时，最新版本为 v0.3.6。')
    ).toBeTruthy()
    const hint = screen.getByTestId('update-auto-failed-hint')
    expect(hint.textContent).toBe(
      '自动更新失败，请手动更新。失败原因：installer size mismatch: ' +
        'expected 720487430, got 41943040'
    )
    // One line: long reasons are cut, the full text is on hover.
    expect(hint.className).toContain('truncate')
    expect(hint.getAttribute('title')).toContain('installer size mismatch')
    const link = screen.getByText('下载最新版本').closest('a')
    expect(link?.getAttribute('href')).toBe(RELEASES)
    // The background-error box of the manual popup is not doubled up.
    expect(screen.queryByText('后台自动更新没有成功')).toBeNull()
    fireEvent.click(screen.getByText('稍后提醒'))
    expect(onClose).toHaveBeenCalled()
  })

  it('mac: the copy command — from the server when the check had none', () => {
    // The very network that broke the download can leave update-check
    // with no route and no version; the popup must still say both.
    renderModal('en', {
      check: check({
        update_available: false,
        latest_version: undefined,
        download_url: null,
      }),
      status: failed({
        state: 'idle',
        error: null,
        platform: 'mac',
        path: 'pip',
        download_failure: failure({ path: 'pip', stage: 'prefetch' }),
        manual_update: {
          upgrade_command: 'vibe-seller upgrade',
          download_url: null,
        },
      }),
    })
    expect(screen.getByText('Update available')).toBeTruthy()
    expect(
      screen.getByText('v0.3.4 is out of date — v0.3.6 is now available.')
    ).toBeTruthy()
    expect(screen.getByTestId('update-auto-failed-hint').textContent).toMatch(
      /^Automatic update failed — please update by hand\. Reason: /
    )
    expect(screen.getByText('vibe-seller upgrade')).toBeTruthy()
    expect(screen.getByText('Copy')).toBeTruthy()
    expect(screen.getByText('Remind me later')).toBeTruthy()
  })

  it('a retry that is downloading shows its progress, not the manual popup', () => {
    renderModal('zh', {
      status: failed({
        state: 'downloading',
        manual_due: false,
        downloaded_bytes: 300 * 1024 * 1024,
        total_bytes: 700 * 1024 * 1024,
      }),
    })
    expect(screen.queryByTestId('update-auto-failed-hint')).toBeNull()
    expect(screen.getByText(/300 \/ 700 MB/)).toBeTruthy()
  })
})

// ── the chip ─────────────────────────────────────────────────────────

describe('the chip after a failed download', () => {
  const renderChip = (s: UpdateStatus, lng: 'en' | 'zh' = 'zh') => {
    const onOpen = vi.fn()
    render(
      <I18nextProvider i18n={make(lng)}>
        <UpdateChip status={s} onOpen={onOpen} />
      </I18nextProvider>
    )
    return onOpen
  }

  it('says the download failed and reopens the guide', () => {
    const onOpen = renderChip(failed({ state: 'idle', error: null }))
    const chip = screen.getByTestId('update-chip')
    expect(chip.textContent).toBe('v0.3.6 自动更新失败 · 点此手动更新')
    expect(chip.className).toContain('bg-red-50')
    fireEvent.click(chip)
    expect(onOpen).toHaveBeenCalled()
  })

  it('en wording', () => {
    renderChip(failed(), 'en')
    expect(screen.getByTestId('update-chip').textContent).toBe(
      'v0.3.6 automatic update failed · update by hand'
    )
  })

  it('a retry downloading: the progress, not the failure', () => {
    renderChip(
      failed({
        state: 'downloading',
        manual_due: false,
        downloaded_bytes: 50,
        total_bytes: 100,
      })
    )
    expect(screen.getByTestId('update-chip').textContent).toBe(
      '正在下载 v0.3.6 · 50%'
    )
  })

  it('nothing standing (updated by hand): no chip', () => {
    renderChip(
      failed({
        state: 'idle',
        error: null,
        version: null,
        download_failure: null,
        manual_due: false,
      })
    )
    expect(screen.queryByTestId('update-chip')).toBeNull()
  })
})

// ── when it pops ─────────────────────────────────────────────────────

const user = { id: 'u1', username: 'admin', role: 'admin' } as AuthUser

function serve(routes: Record<string, () => unknown>) {
  global.fetch = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    const key = Object.keys(routes).find(k => url.startsWith(k))
    if (!key) throw new Error(`unmocked ${url}`)
    return {
      ok: true,
      status: 200,
      json: async () => routes[key](),
    } as unknown as Response
  }) as unknown as typeof fetch
}

const settle = async (ms = 10) => {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms)
  })
}

describe('when the guide pops', () => {
  beforeEach(() => {
    sessionStorage.clear()
    localStorage.setItem(GUIDE_SEEN_KEY, '2026-01-01T00:00:00+00:00')
    vi.useFakeTimers()
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  it('a failed download pops it on load', async () => {
    serve({
      '/api/system/update-check': () => check(),
      '/api/system/update-status': () => failed(),
    })
    const { result } = renderHook(() => useUpdateFlow(user))
    await settle()
    expect(result.current.open).toBe(true)
  })

  it('also after a restart, when the error is gone but the failure stands', async () => {
    serve({
      '/api/system/update-check': () => check(),
      '/api/system/update-status': () =>
        failed({ state: 'idle', error: null, version: null, path: null }),
    })
    const { result } = renderHook(() => useUpdateFlow(user))
    await settle()
    expect(result.current.open).toBe(true)
  })

  it('稍后 holds for this attempt; the next failed attempt pops again', async () => {
    let now = failed()
    serve({
      '/api/system/update-check': () => check(),
      '/api/system/update-status': () => now,
    })
    const { result } = renderHook(() => useUpdateFlow(user))
    await settle()
    act(() => result.current.closeModal())
    await settle(15_000)
    expect(result.current.open).toBe(false)

    // Six hours later the cycle retried: it downloaded for a while …
    now = failed({ state: 'downloading', manual_due: false })
    await settle(15_000)
    expect(result.current.open).toBe(false)
    // … and failed again.
    now = failed({ download_failure: failure({ attempts: 2 }) })
    await settle(15_000)
    expect(result.current.open).toBe(true)
  })

  it('a retry that succeeds hands over to the ready popup', async () => {
    let now = failed()
    serve({
      '/api/system/update-check': () => check(),
      '/api/system/update-status': () => now,
    })
    const { result } = renderHook(() => useUpdateFlow(user))
    await settle()
    act(() => result.current.closeModal())
    now = failed({
      state: 'ready',
      error: null,
      popup_due: true,
      ready_since: 1764140000,
      download_failure: null,
      manual_due: false,
    })
    await settle(15_000)
    // Dismissing the failure did not dismiss the release itself.
    expect(result.current.open).toBe(true)
  })

  it('already current (updated by hand): nothing pops', async () => {
    serve({
      '/api/system/update-check': () => ({
        dev: false,
        update_available: false,
        current_version: '0.3.6',
        channel: 'stable',
      }),
      '/api/system/update-status': () =>
        failed({
          state: 'idle',
          error: null,
          version: null,
          current_version: '0.3.6',
          download_failure: null,
          manual_due: false,
        }),
    })
    const { result } = renderHook(() => useUpdateFlow(user))
    await settle(15_000)
    expect(result.current.open).toBe(false)
  })

  it('an install that cannot update itself keeps its own popup rule', async () => {
    serve({
      '/api/system/update-check': () => check({ update_available: false }),
      '/api/system/update-status': () => failed({ supported: false }),
    })
    const { result } = renderHook(() => useUpdateFlow(user))
    await settle()
    expect(result.current.open).toBe(false)
  })

  it('waits for the first-run guide like every other popup', async () => {
    localStorage.removeItem(GUIDE_SEEN_KEY)
    serve({
      '/api/system/update-check': () => check(),
      '/api/system/update-status': () => failed(),
    })
    const { result } = renderHook(() => useUpdateFlow(user))
    await settle()
    expect(result.current.open).toBe(false)
  })
})
