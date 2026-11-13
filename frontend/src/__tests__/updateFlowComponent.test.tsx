/**
 * <UpdateFlow/> is the App-side mount point of the update modals: the
 * available-update dialog renders only when the check result has
 * arrived AND the flow says open, and the post-update notes render
 * only while the flow says they are due. App.tsx itself stays a
 * one-line call site.
 */
import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { I18nextProvider, initReactI18next } from 'react-i18next'
import i18n from 'i18next'
import enTranslation from '../i18n/locales/en/translation.json'
import zhTranslation from '../i18n/locales/zh/translation.json'
import { UpdateFlow } from '../components/UpdateFlow'
import type {
  ReleaseNotesRange,
  UpdateCheckResult,
  UpdateStatus,
} from '../types'

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
  releases_page_url: 'https://gh/releases',
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
  path: 'installer',
  auto_apply: false,
  popup_due: true,
  ready_since: 1765670400,
  last_update: null,
} as unknown as UpdateStatus

/** What /api/system/release-notes answers for 0.1.0 → 0.3.2. */
const range = {
  since: '0.1.0',
  until: '0.3.2',
  releases: ['0.3.2', '0.2.0'],
  highlights: [{ version: '0.3.2', zh: '- 亮点甲', en: '- Highlight A' }],
  notes: [{ version: '0.2.0', text: '- 需要重启后再配置' }],
} as ReleaseNotesRange

const flow = (over: Record<string, unknown> = {}) => ({
  updateCheck: check,
  status,
  open: true,
  updatedNotes: null,
  updatedOpen: false,
  closeUpdated: vi.fn(),
  openModal: vi.fn(),
  closeModal: vi.fn(),
  restart: vi.fn(),
  restartError: null,
  restartTimedOut: false,
  ...over,
}) as ReturnType<typeof import('../hooks/useUpdateFlow').useUpdateFlow>

const renderFlow = (over: Record<string, unknown> = {}) =>
  render(
    <I18nextProvider i18n={instance}>
      <UpdateFlow flow={flow(over)} isAdmin />
    </I18nextProvider>
  )

describe('UpdateFlow', () => {
  it('renders the modal when open with a check result', () => {
    renderFlow()
    expect(screen.getByText('新版本 v0.3.2 已下载好')).toBeTruthy()
  })

  it('renders nothing while closed, or before the check arrives', () => {
    const { rerender } = renderFlow({ open: false })
    expect(screen.queryByText('新版本 v0.3.2 已下载好')).toBeNull()
    rerender(
      <I18nextProvider i18n={instance}>
        <UpdateFlow flow={flow({ updateCheck: null })} isAdmin />
      </I18nextProvider>
    )
    expect(screen.queryByText('新版本 v0.3.2 已下载好')).toBeNull()
  })

  it('renders the crossed range as "已更新到", with that range\'s notes', () => {
    renderFlow({
      open: false,
      updatedNotes: range,
      updatedOpen: true,
      status: { ...status, state: 'idle', version: null },
    })
    expect(screen.getByText('已更新到 v0.3.2')).toBeTruthy()
    expect(screen.getByText('亮点甲', { exact: false })).toBeTruthy()
    expect(screen.getByText('需要重启后再配置', { exact: false })).toBeTruthy()
    // The range header is the pair the update crossed, not what
    // update-check would call "latest" from the new version.
    expect(
      screen.getByText(/更新内容 · v0\.1\.0 → v0\.3\.2，共 2 个版本/)
    ).toBeTruthy()
    expect(screen.queryByText('新版本 v0.3.2 已下载好')).toBeNull()
  })

  it('never stacks: while the update dialog is up the panel stands down', () => {
    // `updatedOpen` is the flow's own rule (nothing else open), so the
    // mount point has to honour it rather than render both overlays.
    renderFlow({ updatedNotes: range, updatedOpen: false })
    expect(screen.queryByText('已更新到 v0.3.2')).toBeNull()
    expect(screen.getByText('新版本 v0.3.2 已下载好')).toBeTruthy()
  })
})
