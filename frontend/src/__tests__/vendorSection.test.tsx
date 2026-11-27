/**
 * The generic vendor surface: StoreCreationForm's backend options come
 * from the registry (lib/vendorOptions), and VendorSection renders a
 * vendor's credential form / statuses / docs links from its spec — no
 * per-vendor branch anywhere. A FAKE vendor exercises both; the negative
 * controls flip one spec field and assert the opposite outcome.
 */
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { I18nextProvider, initReactI18next } from 'react-i18next'
import i18n from 'i18next'
import enTranslation from '../i18n/locales/en/translation.json'
import zhTranslation from '../i18n/locales/zh/translation.json'
import { VendorSection } from '../components/VendorSection'
import { vendorBackendOptions } from '../lib/vendorOptions'
import type { BrowserVendor, Store, VendorStoreRef } from '../types'

vi.mock('../api', () => ({
  api: {
    get: vi.fn(async () => ({ configured: true, team_name: 'acme', subaccount: 'ops' })),
    post: vi.fn(async () => ({ ok: true })),
  },
}))

function i18nFor(lng: 'en' | 'zh') {
  const inst = i18n.createInstance()
  inst.use(initReactI18next).init({
    resources: { en: { translation: enTranslation }, zh: { translation: zhTranslation } },
    lng, fallbackLng: 'en', interpolation: { escapeValue: false },
  })
  return inst
}

/** A fake vendor in the registry's own shape — the invariant's probe. */
const fakeVendor: BrowserVendor = {
  key: 'fakevendor',
  display_name_en: 'FakeVendor',
  display_name_zh: '假浏览器',
  kind: 'antidetect',
  platforms: ['windows', 'wsl'],
  selectable: true,
  account_cardinality: 'single',
  credential_fields: [
    { key: 'team_name', label_i18n: 'settings.yuehulianTeamName', secret: false, required: true },
    { key: 'subaccount', label_i18n: 'settings.yuehulianSubaccount', secret: false, required: true },
    { key: 'password', label_i18n: 'settings.yuehulianPassword', secret: true, required: true },
  ],
  lists_profiles: true,
  profiles_scope: 'login',
  picker_kind: 'generic',
  i18n_prefix: 'settings.yuehulian',
  docs: {
    errors: {
      topic: 'fakeErrors',
      zh: 'https://docs.vibe-sellers.com/yuehulian/#常见提示--报错',
      en: 'https://docs.vibe-sellers.com/en/yuehulian/#common-statuses--errors',
    },
    one_account: {
      topic: 'fakeOneAccount',
      zh: 'https://docs.vibe-sellers.com/yuehulian/#一台机器只支持一个账号',
      en: 'https://docs.vibe-sellers.com/en/yuehulian/#one-account-per-machine',
    },
  },
}

const renderSection = (over: {
  vendor?: Partial<BrowserVendor>
  stores?: VendorStoreRef[]
  error?: string
  errorMessage?: string
  fetching?: boolean
  allStores?: Store[]
  lng?: 'en' | 'zh'
} = {}) =>
  render(
    <I18nextProvider i18n={i18nFor(over.lng ?? 'en')}>
      <VendorSection
        vendor={{ ...fakeVendor, ...over.vendor }}
        selectedRef="" setSelectedRef={vi.fn()}
        stores={over.stores ?? []}
        error={over.error ?? ''}
        errorMessage={over.errorMessage}
        fetching={over.fetching ?? false}
        fetchStores={vi.fn()}
        allStores={over.allStores ?? ([] as unknown as Store[])}
      />
    </I18nextProvider>,
  )

describe('backend options from the registry', () => {
  const plain = { ...fakeVendor, key: 'chrome', kind: 'plain' as const, platforms: ['windows', 'wsl', 'mac', 'linux'] }

  it('offers selectable vendors for the platform, in registration order', () => {
    const out = vendorBackendOptions([plain, fakeVendor], 'windows')
    expect(out.map(v => v.key)).toEqual(['chrome', 'fakevendor'])
  })

  it('hides a platform-gated vendor off its platforms', () => {
    expect(vendorBackendOptions([fakeVendor], 'mac').map(v => v.key)).toEqual([])
  })

  it('hides non-selectable vendors (deployment-set backends)', () => {
    const hidden = { ...fakeVendor, selectable: false }
    expect(vendorBackendOptions([hidden], 'windows')).toEqual([])
  })

  it('while the platform is unknown, offers only all-platform vendors', () => {
    expect(vendorBackendOptions([plain, fakeVendor], null).map(v => v.key)).toEqual(['chrome'])
  })
})

describe('the generic vendor section', () => {
  it('shows the machine login and the add form from the spec fields', async () => {
    renderSection()
    expect(await screen.findByText('acme / ops')).toBeTruthy()
    fireEvent.click(screen.getByTitle(enTranslation.settings.addAccount))
    expect(screen.getByPlaceholderText(enTranslation.settings.yuehulianTeamName)).toBeTruthy()
    expect(screen.getByPlaceholderText(enTranslation.settings.yuehulianSubaccount)).toBeTruthy()
    expect(screen.getByPlaceholderText(enTranslation.settings.yuehulianPassword)).toBeTruthy()
  })

  it('decodes each structured status into the spec-prefixed string', () => {
    renderSection({ error: 'fakevendor:client_not_running' })
    expect(screen.getByText(enTranslation.settings.yuehulianClientNotRunning)).toBeTruthy()
    renderSection({ error: 'fakevendor:login_required' })
    expect(screen.getByText(enTranslation.settings.yuehulianLoginRequired)).toBeTruthy()
    renderSection({ error: 'fakevendor:no_stores' })
    expect(screen.getByText(enTranslation.settings.yuehulianNoStores)).toBeTruthy()
    renderSection({ error: 'fakevendor:connect_error' })
    expect(screen.getByText(enTranslation.settings.yuehulianLoadFailed)).toBeTruthy()
  })

  it('links the spec-carried docs section next to a status', () => {
    renderSection({ error: 'fakevendor:client_not_running' })
    const link = screen.getByTestId('docs-help-link') as HTMLAnchorElement
    expect(link.dataset.topic).toBe('fakeErrors')
    expect(link.href).toBe(fakeVendor.docs.errors.en)
  })

  it('lists the stores the generic route returned', () => {
    renderSection({
      stores: [
        { name: 'fv-a', platform: 'MarketplaceX', remote_env: 'US' },
        { name: 'fv-b', platform: '', remote_env: '' },
      ],
    })
    const select = screen.getByRole('combobox') as HTMLSelectElement
    expect([...select.options].map(o => o.textContent)).toEqual([
      enTranslation.settings.yuehulianSelectStore,
      'fv-a (MarketplaceX US)',
      'fv-b',
    ])
  })

  it('shows the spinner while fetching with a configured login', async () => {
    renderSection({ fetching: true })
    // The spinner is gated on the configured login (fetched on mount).
    await screen.findByText('acme / ops')
    expect(screen.getByText(enTranslation.settings.yuehulianLoading)).toBeTruthy()
  })

  it('refuses [+] while one of the vendor\'s stores is bound (one account per machine)', async () => {
    const allStores = [{ browser_backend: 'fakevendor' }] as unknown as Store[]
    renderSection({ allStores })
    fireEvent.click(screen.getByTitle(enTranslation.settings.addAccount))
    expect(screen.getByText(enTranslation.settings.yuehulianOneAccount)).toBeTruthy()
    // The form did NOT open.
    expect(screen.queryByPlaceholderText(enTranslation.settings.yuehulianTeamName)).toBeNull()
    // And the refusal links the spec's one_account docs section.
    const link = screen.getByTestId('docs-help-link') as HTMLAnchorElement
    expect(link.dataset.topic).toBe('fakeOneAccount')
  })

  it('a none-cardinality vendor has no account row and fetches on mount', async () => {
    const fetchStores = vi.fn()
    render(
      <I18nextProvider i18n={i18nFor('en')}>
        <VendorSection
          vendor={{ ...fakeVendor, account_cardinality: 'none' }}
          selectedRef="" setSelectedRef={vi.fn()}
          stores={[]} error=""
          fetching={false} fetchStores={fetchStores}
          allStores={[] as unknown as Store[]}
        />
      </I18nextProvider>,
    )
    // No account row, no [+] button, no add form.
    expect(screen.queryByText('No account configured')).toBeNull()
    expect(screen.queryByTitle(enTranslation.settings.addAccount)).toBeNull()
    // Stores are fetched straight away (no configured login to wait for).
    expect(fetchStores).toHaveBeenCalled()
  })

  it('a none-cardinality vendor shows the status hint like any other', () => {
    renderSection({
      vendor: { account_cardinality: 'none', i18n_prefix: 'vendors.nosuchkeys' },
      error: 'fakevendor:client_not_running',
    })
    expect(screen.getByText(
      enTranslation.browserVendor.ClientNotRunning.replace('{{vendor}}', 'FakeVendor'),
    )).toBeTruthy()
  })

  it('twist: a single-cardinality vendor keeps the account row and waits for the login', async () => {
    const fetchStores = vi.fn()
    render(
      <I18nextProvider i18n={i18nFor('en')}>
        <VendorSection
          vendor={fakeVendor}
          selectedRef="" setSelectedRef={vi.fn()}
          stores={[]} error=""
          fetching={false} fetchStores={fetchStores}
          allStores={[] as unknown as Store[]}
        />
      </I18nextProvider>,
    )
    expect(screen.getByText('No account configured')).toBeTruthy()
    expect(screen.getByTitle(enTranslation.settings.addAccount)).toBeTruthy()
    // Not configured → no auto-fetch of the store list.
    expect(fetchStores).not.toHaveBeenCalled()
  })

  it('binds by ref when the vendor reports one, and by name otherwise', () => {
    renderSection({
      stores: [
        { name: 'Pretty Name', platform: '', remote_env: '', ref: 'env-9' },
        { name: 'NoRef Store', platform: '', remote_env: '' },
      ],
    })
    const select = screen.getByRole('combobox') as HTMLSelectElement
    const byValue = [...select.options].map(o => [o.value, o.textContent])
    expect(byValue).toEqual([
      ['', enTranslation.settings.yuehulianSelectStore],
      ['env-9', 'Pretty Name'],
      ['NoRef Store', 'NoRef Store'],
    ])
  })

  it('greys out an unavailable env with its reason instead of hiding it', () => {
    renderSection({
      stores: [
        { name: 'Broken', platform: '', remote_env: '', available: false, unavailable_reason: 'no device bound' },
      ],
    })
    const select = screen.getByRole('combobox') as HTMLSelectElement
    const option = select.options[1]
    expect(option.disabled).toBe(true)
    expect(option.textContent).toContain('no device bound')
  })

  it('falls back to the shared browserVendor strings with the vendor name', () => {
    // A vendor with NO override keys (empty prefix namespace): every
    // status text comes from the shared browserVendor.* keys with
    // {{vendor}} interpolated.
    renderSection({
      vendor: { i18n_prefix: 'vendors.nosuchkeys' },
      error: 'fakevendor:plan_required',
    })
    expect(screen.getByText(
      enTranslation.browserVendor.PlanRequired.replace('{{vendor}}', 'FakeVendor'),
    )).toBeTruthy()
  })

  it('a vendor override beats the shared string', () => {
    // The fixture's prefix points at the yuehulian keys, which exist:
    // the override wins over browserVendor.NoStores.
    renderSection({ error: 'fakevendor:no_stores' })
    expect(screen.getByText(enTranslation.settings.yuehulianNoStores)).toBeTruthy()
  })

  it('a status with no key at all falls back to the backend message', () => {
    renderSection({ error: 'fakevendor:odd_status', errorMessage: 'the vendor said no' })
    expect(screen.getByText('the vendor said no')).toBeTruthy()
  })

  it('without a message either, the load-failed default shows', () => {
    renderSection({ error: 'fakevendor:odd_status' })
    expect(screen.getByText(
      enTranslation.browserVendor.LoadFailed.replace('{{vendor}}', 'FakeVendor'),
    )).toBeTruthy()
  })

  it('renders the strings of the UI language from the same spec prefix', () => {
    renderSection({ error: 'fakevendor:no_stores', lng: 'zh' })
    expect(screen.getByText(zhTranslation.settings.yuehulianNoStores)).toBeTruthy()
  })
})
