/**
 * Sidebar footer chip: always visible while an in-app update exists
 * (downloading / ready / restarting), shows progress, and opens the
 * modal on click. Hidden for unsupported installs, when idle, and on
 * error.
 *
 * The chip is the pip path's ONLY ambient signal — that update applies
 * itself with no popup — so its exact wording per path is the contract
 * under test here, along with the one case where it must appear while
 * the machine is otherwise idle: the apply that failed or rolled back.
 */
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { I18nextProvider, initReactI18next } from 'react-i18next'
import i18n from 'i18next'
import enTranslation from '../i18n/locales/en/translation.json'
import zhTranslation from '../i18n/locales/zh/translation.json'
import { UpdateChip } from '../components/UpdateChip'
import type { LastUpdate, UpdateStatus } from '../types'

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

const MB = 1024 * 1024

const lastUpdate = (over: Partial<LastUpdate> = {}): LastUpdate => ({
  status: 'ok',
  from: '0.1.0',
  to: '0.3.2',
  at: '2026-11-13T00:00:00Z',
  path: 'pip',
  trigger: 'auto_idle',
  stage: null,
  error: null,
  error_class: null,
  seconds: 12,
  ...over,
})

const status = (over: Partial<UpdateStatus> = {}): UpdateStatus => ({
  supported: true,
  state: 'ready',
  version: '0.3.2',
  current_version: '0.1.0',
  downloaded_bytes: null,
  total_bytes: null,
  error: null,
  running_tasks: 0,
  platform: 'windows',
  // Installer path by default: that is the one that waits for a click,
  // so the pip cases say so on purpose.
  path: 'installer',
  auto_apply: false,
  popup_due: true,
  ready_since: 1765670400,
  last_update: null,
  refusal: null,
  ...over,
})

const pip = (over: Partial<UpdateStatus> = {}) =>
  status({ path: 'pip', auto_apply: true, popup_due: false, ...over })

const renderChip = (s: UpdateStatus | null, lng: 'en' | 'zh' = 'zh') => {
  const onOpen = vi.fn()
  render(
    <I18nextProvider i18n={make(lng)}>
      <UpdateChip status={s} onOpen={onOpen} />
    </I18nextProvider>
  )
  return onOpen
}

const chipText = () => screen.getByTestId('update-chip').textContent

describe('sidebar update chip', () => {
  it('ready on the installer path: the restart wording; click opens the modal', () => {
    const onOpen = renderChip(status())
    const chip = screen.getByTestId('update-chip')
    expect(chip.textContent).toBe('v0.3.2 已下载好 · 重启以更新')
    fireEvent.click(chip)
    expect(onOpen).toHaveBeenCalledTimes(1)
  })

  it('ready on the pip path: it says the update applies itself, no click asked', () => {
    renderChip(pip())
    expect(chipText()).toBe('v0.3.2 已下载，空闲时自动更新')
  })

  it('ready on the pip path (en): the same promise in English', () => {
    renderChip(pip(), 'en')
    expect(chipText()).toBe(
      'v0.3.2 is downloaded — updates automatically when idle'
    )
  })

  it('downloading on Windows: percent in the label and a sized bar', () => {
    renderChip(
      status({
        state: 'downloading',
        downloaded_bytes: Math.floor(603.5 * MB),
        total_bytes: 701 * MB,
      })
    )
    const chip = screen.getByTestId('update-chip')
    expect(chip.textContent).toContain('正在下载 v0.3.2 · 86%')
    const bar = chip.querySelector('[style*="width"]')
    expect(bar?.getAttribute('style')).toContain('width: 86%')
  })

  it('downloading on mac/linux: no percent, indeterminate bar', () => {
    renderChip(status({ state: 'downloading', platform: 'linux' }), 'en')
    const chip = screen.getByTestId('update-chip')
    expect(chip.textContent).toContain('Downloading v0.3.2…')
    expect(chip.textContent).not.toMatch(/\d+%/)
  })

  it('restarting: busy label', () => {
    renderChip(status({ state: 'restarting' }))
    expect(screen.getByTestId('update-chip').textContent).toBe(
      '正在更新到 v0.3.2…'
    )
  })

  it('rolled-back apply while idle: names the version still running', () => {
    const onOpen = renderChip(
      status({
        state: 'idle',
        version: null,
        last_update: lastUpdate({ status: 'rolled_back' }),
      })
    )
    expect(chipText()).toBe('更新失败，已恢复到 v0.1.0')
    // The chip is how the admin finds out, so it still opens the dialog.
    fireEvent.click(screen.getByTestId('update-chip'))
    expect(onOpen).toHaveBeenCalledTimes(1)
  })

  it('rolled-back apply (en): "the update failed — vX was restored"', () => {
    renderChip(
      status({
        state: 'idle',
        version: null,
        last_update: lastUpdate({ status: 'rolled_back' }),
      }),
      'en'
    )
    expect(chipText()).toBe('The update failed — v0.1.0 was restored')
  })

  it('failed apply with no rollback: a plain failure about the target', () => {
    renderChip(
      status({
        state: 'idle',
        version: null,
        last_update: lastUpdate({ status: 'failed' }),
      })
    )
    expect(chipText()).toBe('v0.3.2 更新失败')
  })

  it('a successful apply is not reported as news', () => {
    renderChip(status({ state: 'idle', version: null, last_update: null }))
    expect(screen.queryByTestId('update-chip')).toBeNull()
  })

  it('an apply in flight outranks the previous failure', () => {
    renderChip(pip({ last_update: lastUpdate({ status: 'rolled_back' }) }))
    // The machine already re-staged: what it says now is the ready line,
    // not last cycle's failure.
    expect(chipText()).toBe('v0.3.2 已下载，空闲时自动更新')
  })

  it('hidden when idle, on error, unsupported, or no status', () => {
    for (const s of [
      status({ state: 'idle' }),
      status({ state: 'error' }),
      status({ supported: false }),
      status({ state: 'ready', version: null }),
      null,
    ]) {
      const { unmount } = render(
        <I18nextProvider i18n={make()}>
          <UpdateChip status={s} onOpen={() => {}} />
        </I18nextProvider>
      )
      expect(screen.queryByTestId('update-chip')).toBeNull()
      unmount()
    }
  })
})

describe('chip — a release this machine already put back', () => {
  /** The refusal for the version on offer: `auto_apply` false, and the
   *  record of what went wrong beside it. */
  const refused = (lngOver: Partial<UpdateStatus> = {}) =>
    pip({
      auto_apply: false,
      popup_due: true,
      last_update: lastUpdate({
        status: 'rolled_back',
        to: '0.3.2',
        stage: 'health',
        error: 'the server never reported 0.3.2',
      }),
      refusal: { version: '0.3.2', status: 'rolled_back', stage: 'health' },
      ...lngOver,
    })

  it('zh: says it will not be retried, not that it will update by itself', () => {
    renderChip(refused())
    const text = chipText() ?? ''
    expect(text).toContain('不会再自动重试')
    expect(text).not.toContain('空闲时自动更新')
  })

  it('en: the same promise withdrawn', () => {
    renderChip(refused(), 'en')
    const text = chipText() ?? ''
    expect(text).toContain('will not be retried on its own')
    expect(text).not.toContain('updates automatically when idle')
  })

  it('a DIFFERENT version on offer keeps the promise', () => {
    renderChip(pip({ auto_apply: true, popup_due: false, refusal: null }))
    expect(chipText()).toContain('空闲时自动更新')
  })
})
