/**
 * <UpdateFlow/> is the App-side mount point of the update modal: it
 * renders only when the check result has arrived AND the flow says
 * open — App.tsx itself stays a one-line call site.
 */
import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { I18nextProvider, initReactI18next } from 'react-i18next'
import i18n from 'i18next'
import enTranslation from '../i18n/locales/en/translation.json'
import zhTranslation from '../i18n/locales/zh/translation.json'
import { UpdateFlow } from '../components/UpdateFlow'
import type { UpdateCheckResult, UpdateStatus } from '../types'

const instance = i18n.createInstance()
instance.use(initReactI18next).init({
  lng: 'zh',
  resources: {
    en: { translation: enTranslation },
    zh: { translation: zhTranslation },
  },
  interpolation: { escapeValue: false },
})

const check = {
  dev: false,
  update_available: true,
  current_version: '0.1.0',
  latest_version: '0.3.2',
  in_app_update: true,
  releases: [],
  highlights: [],
  notes: [],
} as unknown as UpdateCheckResult

const status = {
  supported: true,
  state: 'ready',
  version: '0.3.2',
  current_version: '0.1.0',
  downloaded_bytes: null,
  total_bytes: null,
  error: null,
  running_tasks: 0,
  platform: 'linux',
} as UpdateStatus

const flow = (over: Record<string, unknown> = {}) => ({
  updateCheck: check,
  status,
  open: true,
  openModal: vi.fn(),
  closeModal: vi.fn(),
  restart: vi.fn(),
  restartError: null,
  restartTimedOut: false,
  ...over,
}) as ReturnType<typeof import('../hooks/useUpdateFlow').useUpdateFlow>

describe('UpdateFlow', () => {
  it('renders the modal when open with a check result', () => {
    render(
      <I18nextProvider i18n={instance}>
        <UpdateFlow flow={flow()} isAdmin />
      </I18nextProvider>
    )
    expect(screen.getByText('新版本 v0.3.2 已下载好')).toBeTruthy()
  })

  it('renders nothing while closed, or before the check arrives', () => {
    const { rerender } = render(
      <I18nextProvider i18n={instance}>
        <UpdateFlow flow={flow({ open: false })} isAdmin />
      </I18nextProvider>
    )
    expect(screen.queryByText('新版本 v0.3.2 已下载好')).toBeNull()
    rerender(
      <I18nextProvider i18n={instance}>
        <UpdateFlow flow={flow({ updateCheck: null })} isAdmin />
      </I18nextProvider>
    )
    expect(screen.queryByText('新版本 v0.3.2 已下载好')).toBeNull()
  })
})
