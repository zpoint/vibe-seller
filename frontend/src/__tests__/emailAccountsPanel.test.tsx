/**
 * Render tests for the email accounts settings panel.
 *
 * The "Test Connection" API returns the provider's real failure
 * reason (``{ok: false, message: 'IMAP login failed: …'}``) and save
 * failures carry a ``detail`` string. Both must reach the user: a
 * bare "Connection failed" leaves them guessing between a wrong
 * authorization code and a mistyped server.
 *
 * The save failures come back as fixed English strings; the known
 * ones are mapped to i18n keys so the zh UI never shows raw English.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { I18nextProvider, initReactI18next } from 'react-i18next'
import i18n from 'i18next'
import enTranslation from '../i18n/locales/en/translation.json'
import zhTranslation from '../i18n/locales/zh/translation.json'

const mockApi = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  del: vi.fn(),
  patch: vi.fn(),
}))
vi.mock('../api', () => ({ api: mockApi }))

import { EmailAccountsPanel } from '../components/settings/EmailAccountsPanel'

function makeInstance(lng: 'en' | 'zh') {
  const inst = i18n.createInstance()
  inst.use(initReactI18next).init({
    resources: {
      en: { translation: enTranslation },
      zh: { translation: zhTranslation },
    },
    lng,
    fallbackLng: 'en',
    interpolation: { escapeValue: false },
  })
  return inst
}

function renderPanel(lng: 'en' | 'zh' = 'en') {
  return render(
    <I18nextProvider i18n={makeInstance(lng)}>
      <EmailAccountsPanel
        emailAccounts={[]}
        loadEmailAccounts={vi.fn()}
        stores={[]}
        isAdmin={false}
      />
    </I18nextProvider>,
  )
}

const ADD_LABEL = { en: '+ Add Email', zh: '+ 添加邮箱' }
const SAVE_LABEL = { en: 'Save', zh: '保存' }
const PASSWORD_PLACEHOLDER = {
  en: 'Password/App Key',
  zh: '密码/授权码',
}

async function openAddForm(lng: 'en' | 'zh' = 'en') {
  renderPanel(lng)
  fireEvent.click(screen.getByText(ADD_LABEL[lng]))
  fireEvent.change(screen.getByPlaceholderText('user@example.com'), {
    target: { value: 'demo@example.com' },
  })
  fireEvent.change(screen.getByPlaceholderText('imap.gmail.com'), {
    target: { value: 'imap.example.com' },
  })
  fireEvent.change(
    screen.getAllByPlaceholderText(PASSWORD_PLACEHOLDER[lng])[0],
    { target: { value: 'abcd efgh ijkl mnop' } },
  )
}

describe('EmailAccountsPanel failure reasons', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    // Default: store-link lookups etc. succeed.
    mockApi.get.mockResolvedValue([])
  })

  it('shows the provider error text when Test Connection fails', async () => {
    mockApi.post.mockResolvedValue({
      ok: false,
      message: "IMAP login failed: b'LOGIN Login error or password error'",
    })
    await openAddForm()
    fireEvent.click(screen.getByText('Test Connection'))
    await waitFor(() =>
      screen.getByText((content) => content.includes('LOGIN Login error')),
    )
  })

  it('surfaces the API error when saving fails', async () => {
    mockApi.post.mockRejectedValue(new Error('Email account already exists'))
    await openAddForm()
    fireEvent.click(screen.getByText('Save'))
    await waitFor(() =>
      screen.getByText((content) =>
        content.includes('already added'),
      ),
    )
  })

  it('localizes known save errors instead of raw English (zh UI)', async () => {
    mockApi.post.mockRejectedValue(new Error('Email account already exists'))
    await openAddForm('zh')
    fireEvent.click(screen.getByText(SAVE_LABEL.zh))
    await waitFor(() => screen.getByText('这个邮箱已经添加过了'))
    // The raw API string must not leak into the zh UI.
    expect(
      screen.queryByText((content) =>
        content.includes('Email account already exists'),
      ),
    ).toBeNull()
  })
})
