/**
 * Render tests for the email accounts settings panel.
 *
 * The "Test Connection" API returns the provider's real failure
 * reason (``{ok: false, message: 'IMAP login failed: …'}``) and save
 * failures carry a ``detail`` string. Both must reach the user: a
 * bare "Connection failed" leaves them guessing between a wrong
 * authorization code and a mistyped server.
 */
import { describe, it, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { I18nextProvider, initReactI18next } from 'react-i18next'
import i18n from 'i18next'
import enTranslation from '../i18n/locales/en/translation.json'

const mockApi = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  del: vi.fn(),
  patch: vi.fn(),
}))
vi.mock('../api', () => ({ api: mockApi }))

import { EmailAccountsPanel } from '../components/settings/EmailAccountsPanel'

const i18nTestInstance = i18n.createInstance()
i18nTestInstance.use(initReactI18next).init({
  resources: { en: { translation: enTranslation } },
  lng: 'en',
  fallbackLng: 'en',
  interpolation: { escapeValue: false },
})

function renderPanel() {
  return render(
    <I18nextProvider i18n={i18nTestInstance}>
      <EmailAccountsPanel
        emailAccounts={[]}
        loadEmailAccounts={vi.fn()}
        stores={[]}
        isAdmin={false}
      />
    </I18nextProvider>,
  )
}

async function openAddForm() {
  renderPanel()
  fireEvent.click(screen.getByText('+ Add Email'))
  fireEvent.change(screen.getByPlaceholderText('user@example.com'), {
    target: { value: 'demo@example.com' },
  })
  fireEvent.change(screen.getByPlaceholderText('imap.gmail.com'), {
    target: { value: 'imap.example.com' },
  })
  fireEvent.change(
    screen.getAllByPlaceholderText('Password/App Key')[0],
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
        content.includes('Email account already exists'),
      ),
    )
  })
})
