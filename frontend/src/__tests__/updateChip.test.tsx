/**
 * Sidebar footer chip: always visible while an in-app update exists
 * (downloading / ready / restarting), shows progress, and opens the
 * modal on click. Hidden for unsupported installs, when idle, and on
 * error.
 */
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { I18nextProvider, initReactI18next } from 'react-i18next'
import i18n from 'i18next'
import enTranslation from '../i18n/locales/en/translation.json'
import zhTranslation from '../i18n/locales/zh/translation.json'
import { UpdateChip } from '../components/UpdateChip'
import type { UpdateStatus } from '../types'

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
  ...over,
})

const renderChip = (s: UpdateStatus | null, lng: 'en' | 'zh' = 'zh') => {
  const onOpen = vi.fn()
  render(
    <I18nextProvider i18n={make(lng)}>
      <UpdateChip status={s} onOpen={onOpen} />
    </I18nextProvider>
  )
  return onOpen
}

describe('sidebar update chip', () => {
  it('ready (zh): accent text from the mockup; click opens the modal', () => {
    const onOpen = renderChip(status())
    const chip = screen.getByTestId('update-chip')
    expect(chip.textContent).toBe('v0.3.2 已下载好 · 重启以更新')
    fireEvent.click(chip)
    expect(onOpen).toHaveBeenCalledTimes(1)
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
    renderChip(status({ state: 'downloading', platform: 'mac' }), 'en')
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
