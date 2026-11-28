/**
 * The generic vendor surface: StoreCreationForm's backend options come
 * from the registry (lib/vendorOptions), and VendorSection renders a
 * vendor's credential form / statuses / docs links from its spec — no
 * per-vendor branch anywhere. A FAKE vendor exercises both; the negative
 * controls flip one spec field and assert the opposite outcome.
 */
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { I18nextProvider, initReactI18next } from 'react-i18next'
import i18n from 'i18next'
import enTranslation from '../i18n/locales/en/translation.json'
import zhTranslation from '../i18n/locales/zh/translation.json'
import { VendorSection } from '../components/VendorSection'
import { vendorBackendOptions } from '../lib/vendorOptions'
import { api } from '../api'
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

afterEach(() => {
  vi.clearAllMocks()
  vi.mocked(api.get).mockImplementation(
    async () => ({ configured: true, team_name: 'acme', subaccount: 'ops' }),
  )
  vi.mocked(api.post).mockImplementation(async () => ({ ok: true }))
})

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
  selectedRef?: string
} = {}) =>
  render(
    <I18nextProvider i18n={i18nFor(over.lng ?? 'en')}>
      <VendorSection
        vendor={{ ...fakeVendor, ...over.vendor }}
        selectedRef={over.selectedRef ?? ''} setSelectedRef={vi.fn()}
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
  it('shows the machine login and an empty add form from the spec fields', async () => {
    renderSection()
    expect(await screen.findByText('acme / ops')).toBeTruthy()
    const add = screen.getByTitle(enTranslation.settings.addAccount) as HTMLButtonElement
    expect(add.disabled).toBe(false)
    fireEvent.click(add)
    const fields = [
      enTranslation.settings.yuehulianTeamName,
      enTranslation.settings.yuehulianSubaccount,
      enTranslation.settings.yuehulianPassword,
    ].map(label => screen.getByPlaceholderText(label) as HTMLInputElement)
    expect(fields.map(field => field.value)).toEqual(['', '', ''])
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
      'fv-a · US · MarketplaceX',
      'fv-b',
    ])
  })

  it('renders the picker options through the shared label builder', () => {
    // The picker's label rule lives in ONE place
    // (lib/vendorStoreLabel), decided only from the row's fields:
    // listed by name → name first; opaque ref → display · platform ·
    // ref.
    renderSection({
      stores: [
        { name: 'hidden name', platform: 'www.amazon.com',
          remote_env: '#4 美国站 (分组)', ref: 'p0000001' },
        // 悦互联's shape: no ref — the row lists by its NAME, which
        // leads the label (optB).
        { name: '示例甲1店', platform: 'www.amazon.com',
          remote_env: '#4 美国站 (分组)', ref: '' },
        { name: 'fv-noref', platform: '', remote_env: '' },
      ],
    })
    const select = screen.getByRole('combobox') as HTMLSelectElement
    expect([...select.options].map(o => o.textContent)).toEqual([
      enTranslation.settings.yuehulianSelectStore,
      '#4 美国站 (分组) · www.amazon.com · p0000001',
      '示例甲1店 · #4 美国站 (分组) · www.amazon.com',
      'fv-noref',
    ])
  })

  it('shows the spinner while fetching with a configured login', async () => {
    renderSection({ fetching: true })
    // The spinner is gated on the configured login (fetched on mount).
    await screen.findByText('acme / ops')
    expect(screen.getByText(enTranslation.settings.yuehulianLoading)).toBeTruthy()
  })

  it('disables [+] with a tooltip while bound, without an unsolicited error', async () => {
    const allStores = [{ browser_backend: 'fakevendor' }] as unknown as Store[]
    renderSection({ allStores })
    const add = screen.getByRole('button', { name: enTranslation.settings.addAccount }) as HTMLButtonElement
    expect(add.disabled).toBe(true)
    expect(add.title).toBe(enTranslation.settings.yuehulianOneAccount)
    fireEvent.click(add)
    // No error appears before the user actually does anything; the form
    // stays closed and the explanation is available on the disabled button.
    expect(screen.queryByText(enTranslation.settings.yuehulianOneAccount)).toBeNull()
    expect(screen.queryByPlaceholderText(enTranslation.settings.yuehulianTeamName)).toBeNull()
    expect(screen.queryByTestId('docs-help-link')).toBeNull()
  })

  it('create → delete → re-add opens an empty form, never the old account', async () => {
    let credentials: Record<string, unknown> = {
      configured: true, team_name: 'acme', subaccount: 'ops',
    }
    vi.mocked(api.get).mockImplementation(async () => credentials)
    vi.mocked(api.post).mockImplementation(async (_url, data) => {
      const values = data as Record<string, string>
      credentials = { configured: true, ...values }
      return { ok: true }
    })

    const created = renderSection()
    expect(await screen.findByText('acme / ops')).toBeTruthy()
    fireEvent.click(screen.getByTitle(enTranslation.settings.addAccount))
    fireEvent.change(screen.getByPlaceholderText(enTranslation.settings.yuehulianTeamName), {
      target: { value: 'fresh-team' },
    })
    fireEvent.change(screen.getByPlaceholderText(enTranslation.settings.yuehulianSubaccount), {
      target: { value: 'fresh-user' },
    })
    fireEvent.change(screen.getByPlaceholderText(enTranslation.settings.yuehulianPassword), {
      target: { value: 'fresh-password' },
    })
    fireEvent.click(screen.getByText(enTranslation.common.save))
    expect(await screen.findByText('fresh-team / fresh-user')).toBeTruthy()
    created.unmount()

    // The account was used to create a store. While that store exists,
    // [+] cannot open the form.
    const allStores = [{ browser_backend: 'fakevendor' }] as unknown as Store[]
    const bound = renderSection({ allStores })
    const disabledAdd = screen.getByRole('button', { name: enTranslation.settings.addAccount }) as HTMLButtonElement
    expect(disabledAdd.disabled).toBe(true)
    bound.unmount()

    // Deleting that last store cleared every credential row server-side.
    credentials = { configured: false, team_name: '', subaccount: '' }
    renderSection()
    expect(await screen.findByText('No account configured')).toBeTruthy()
    fireEvent.click(screen.getByTitle(enTranslation.settings.addAccount))
    const fields = [
      enTranslation.settings.yuehulianTeamName,
      enTranslation.settings.yuehulianSubaccount,
      enTranslation.settings.yuehulianPassword,
    ].map(label => screen.getByPlaceholderText(label) as HTMLInputElement)
    expect(fields.map(field => field.value)).toEqual(['', '', ''])
    expect(document.body.textContent).not.toContain('acme / ops')
    expect(document.body.textContent).not.toContain('fresh-team / fresh-user')
  })

  it('a resident section clears stale state on the last-store transition', async () => {
    let credentials: Record<string, unknown> = {
      configured: true, team_name: 'acme', subaccount: 'ops',
    }
    vi.mocked(api.get).mockImplementation(async () => credentials)
    const setSelectedRef = vi.fn()
    const fetchStores = vi.fn()
    const resetStores = vi.fn()
    const translation = i18nFor('en')
    const profile = { name: 'old-profile', platform: '', remote_env: '', ref: 'old-ref' }
    const ui = (allStores: Store[]) => (
      <I18nextProvider i18n={translation}>
        <VendorSection
          vendor={fakeVendor}
          selectedRef="old-ref" setSelectedRef={setSelectedRef}
          stores={[profile]} error="" fetching={false}
          fetchStores={fetchStores} resetStores={resetStores}
          allStores={allStores}
        />
      </I18nextProvider>
    )
    const bound = [{ browser_backend: 'fakevendor' }] as unknown as Store[]
    const view = render(ui(bound))
    expect(await screen.findByText('acme / ops')).toBeTruthy()
    expect(fetchStores).toHaveBeenCalledTimes(1)

    // StoresPanel deletes the last store while this exact component stays
    // mounted. The parent has not yet rerendered its profile props.
    credentials = { configured: false, team_name: '', subaccount: '' }
    view.rerender(ui([]))
    expect(await screen.findByText('No account configured')).toBeTruthy()
    expect(setSelectedRef).toHaveBeenLastCalledWith('')
    expect(resetStores).toHaveBeenCalled()
    expect(screen.queryByRole('combobox')).toBeNull()
    await waitFor(() => expect(api.get).toHaveBeenCalledTimes(2))
    // The unconfigured account must not trigger another profile fetch or a
    // credential-refresh effect loop.
    expect(fetchStores).toHaveBeenCalledTimes(1)

    fireEvent.click(screen.getByTitle(enTranslation.settings.addAccount))
    const fields = [
      enTranslation.settings.yuehulianTeamName,
      enTranslation.settings.yuehulianSubaccount,
      enTranslation.settings.yuehulianPassword,
    ].map(label => screen.getByPlaceholderText(label) as HTMLInputElement)
    expect(fields.map(field => field.value)).toEqual(['', '', ''])
  })

  it('switching vendors resets form values and secret visibility without remounting', async () => {
    const setSelectedRef = vi.fn()
    const resetStores = vi.fn()
    const translation = i18nFor('en')
    const otherVendor = { ...fakeVendor, key: 'othervendor' }
    const ui = (vendor: BrowserVendor) => (
      <I18nextProvider i18n={translation}>
        <VendorSection
          vendor={vendor}
          selectedRef="old-ref" setSelectedRef={setSelectedRef}
          stores={[]} error="" fetching={false}
          fetchStores={vi.fn()} resetStores={resetStores}
          allStores={[]}
        />
      </I18nextProvider>
    )
    const view = render(ui(fakeVendor))
    expect(await screen.findByText('acme / ops')).toBeTruthy()
    fireEvent.click(screen.getByTitle(enTranslation.settings.addAccount))
    const team = screen.getByPlaceholderText(enTranslation.settings.yuehulianTeamName)
    const password = screen.getByPlaceholderText(enTranslation.settings.yuehulianPassword) as HTMLInputElement
    fireEvent.change(team, { target: { value: 'shared-user' } })
    fireEvent.change(password, { target: { value: 'shared-password' } })
    fireEvent.click(password.parentElement?.querySelector('button') as HTMLButtonElement)
    expect(password.type).toBe('text')

    view.rerender(ui(otherVendor))
    await waitFor(() => expect(api.get).toHaveBeenCalledWith('/api/browser-vendors/othervendor/credentials'))
    expect(setSelectedRef).toHaveBeenLastCalledWith('')
    expect(resetStores).toHaveBeenCalled()
    expect(screen.queryByPlaceholderText(enTranslation.settings.yuehulianTeamName)).toBeNull()

    fireEvent.click(screen.getByTitle(enTranslation.settings.addAccount))
    const freshTeam = screen.getByPlaceholderText(enTranslation.settings.yuehulianTeamName) as HTMLInputElement
    const freshPassword = screen.getByPlaceholderText(enTranslation.settings.yuehulianPassword) as HTMLInputElement
    expect(freshTeam.value).toBe('')
    expect(freshPassword.value).toBe('')
    expect(freshPassword.type).toBe('password')
  })

  it('a credential-less single vendor has no account row and fetches on mount', async () => {
    const fetchStores = vi.fn()
    render(
      <I18nextProvider i18n={i18nFor('en')}>
        <VendorSection
          vendor={{ ...fakeVendor, account_cardinality: 'single', credential_fields: [] }}
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

  it('a credential-less vendor shows the status hint like any other', () => {
    renderSection({
      vendor: { account_cardinality: 'single', credential_fields: [], i18n_prefix: 'vendors.nosuchkeys' },
      error: 'fakevendor:client_not_running',
    })
    expect(screen.getByText(
      enTranslation.browserVendor.ClientNotRunning.replace('{{vendor}}', 'FakeVendor'),
    )).toBeTruthy()
  })

  it('twist: a vendor WITH credential fields keeps the account row and waits for the login', async () => {
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
      ['env-9', 'Pretty Name · env-9'],
      ['NoRef Store', 'NoRef Store'],
    ])
  })

  it('translates the unavailability token and puts the detail in the tooltip', () => {
    renderSection({
      stores: [
        { name: 'Broken', platform: '', remote_env: '', available: false,
          unavailable_reason: 'no_device', unavailable_detail: '机器 A 未绑定' },
      ],
    })
    const option = (screen.getByRole('combobox') as HTMLSelectElement).options[1]
    // The label shows the TRANSLATED token, never the raw token or the
    // vendor-language detail.
    expect(option.textContent).toBe('Broken — no device bound')
    expect(option.textContent).not.toContain('no_device')
    expect(option.textContent).not.toContain('机器 A 未绑定')
    // The vendor's own words ride the tooltip.
    expect(option.title).toBe('机器 A 未绑定')
  })

  it('an unavailable row with no detail falls back to its full label as the tooltip', () => {
    renderSection({
      stores: [
        { name: 'Bare', platform: '', remote_env: '', available: false,
          unavailable_reason: 'expired' },
      ],
    })
    const option = (screen.getByRole('combobox') as HTMLSelectElement).options[1]
    expect(option.title).toBe('Bare — expired')
  })

  it('an unknown token falls back to the detail, then the generic string', () => {
    renderSection({
      stores: [
        { name: 'Odd', platform: '', remote_env: '', available: false,
          unavailable_reason: 'nosuchtoken', unavailable_detail: 'vendor said why' },
      ],
    })
    let option = (screen.getByRole('combobox') as HTMLSelectElement).options[1]
    expect(option.textContent).toContain(' — vendor said why')
    // And with neither a token nor a detail, the generic 'other' string.
    renderSection({
      stores: [
        { name: 'Bare', platform: '', remote_env: '', available: false },
      ],
    })
    option = (screen.getAllByRole('combobox').at(-1) as HTMLSelectElement).options[1]
    expect(option.textContent).toContain(' — cannot open right now')
  })

  it('drops empty label parts with no dangling separator', () => {
    renderSection({
      stores: [
        { name: 'test1', platform: 'amazon.com', remote_env: '' },
        { name: 'test2', platform: '', remote_env: 'US' },
        { name: 'test3', platform: '', remote_env: '' },
      ],
    })
    const select = screen.getByRole('combobox') as HTMLSelectElement
    expect([...select.options].map(o => o.textContent)).toEqual([
      enTranslation.settings.yuehulianSelectStore,
      'test1 · amazon.com',   // no separator where the platform would be
      'test2 · US',           // listed by name: the name leads, env follows
      'test3',                // both empty: the name alone
    ])
  })

  it('greys out an unavailable env with its reason instead of hiding it', () => {
    renderSection({
      stores: [
        { name: 'Broken', platform: '', remote_env: '', available: false, unavailable_reason: 'expired' },
      ],
    })
    const select = screen.getByRole('combobox') as HTMLSelectElement
    const option = select.options[1]
    expect(option.disabled).toBe(true)
    expect(option.textContent).toContain('Broken — expired')
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

describe('the selected row line under the picker (option A)', () => {
  // Two same-named rows that only their ref tells apart — the closed
  // select cannot, the 已选 line can.
  const sameName = [
    { name: '示例甲1店-长名称演示', platform: 'host.example.com',
      remote_env: '', ref: 'kaaaaaa1' },
    { name: '示例甲1店-长名称演示', platform: 'host.example.com',
      remote_env: '', ref: 'kaaaaaa2' },
  ]

  it('(a) selecting the SECOND same-named row names its ref', () => {
    renderSection({ stores: sameName, selectedRef: 'kaaaaaa2' })
    const parts = screen.getAllByTestId('vendor-selected-part')
    expect(parts.map(p => p.textContent)).toEqual([
      '示例甲1店-长名称演示', 'host.example.com', 'kaaaaaa2',
    ])
  })

  it('(a-zh) the line speaks the UI language', () => {
    renderSection({ stores: sameName, selectedRef: 'kaaaaaa1', lng: 'zh' })
    const line = screen.getByTestId('vendor-selected-line')
    expect(line.textContent).toContain('已选：')
    expect(screen.getAllByTestId('vendor-selected-part').map(p => p.textContent))
      .toEqual(['示例甲1店-长名称演示', 'host.example.com', 'kaaaaaa1'])
  })

  it('(b) the line appears only while a row is selected', () => {
    const first = renderSection({ stores: sameName })
    expect(screen.queryByTestId('vendor-selected-line')).toBeNull()
    first.unmount()
    renderSection({ stores: sameName, selectedRef: 'kaaaaaa1' })
    expect(screen.getByTestId('vendor-selected-line')).toBeTruthy()
  })

  it('(c) a selectable option carries the full label as its tooltip; a no-detail unavailable row falls back to it', () => {
    renderSection({
      stores: [
        { name: 'hidden', platform: 'www.amazon.com',
          remote_env: '#4 美国站 (分组)', ref: 'p0000001' },
        { name: '示例乙1店', platform: '', remote_env: '',
          available: false, unavailable_reason: 'expired' },
      ],
    })
    const select = screen.getByRole('combobox') as HTMLSelectElement
    expect(select.options[1].title).toBe('#4 美国站 (分组) · www.amazon.com · p0000001')
    expect(select.options[2].title).toBe('示例乙1店 — expired')
  })

  it('(d) each label part is its own element — the line wraps at the separators, never mid-word', () => {
    renderSection({ stores: sameName, selectedRef: 'kaaaaaa2' })
    const parts = screen.getAllByTestId('vendor-selected-part')
    expect(parts).toHaveLength(3)
    for (const p of parts) {
      // An inline-block moves whole to the next line; a part longer
      // than the column may still wrap inside itself (overflow-wrap
      // anywhere on that span only).
      expect(p.className).toContain('inline-block')
      expect((p as HTMLElement).style.overflowWrap).toBe('anywhere')
    }
  })
})

describe('the account row of an all-secret vendor', () => {
  // A vendor whose credential fields are ALL secret (e.g. an API key):
  // once configured there is nothing that may be shown, so the row says
  // 已配置/Configured — never a part of the key.
  const secretOnly: BrowserVendor = {
    ...fakeVendor,
    key: 'secretvendor',
    credential_fields: [
      { key: 'api_key', label_i18n: 'settings.adspowerApiKey', secret: true, required: true },
    ],
  }
  const defaultStatus = { configured: true, team_name: 'acme', subaccount: 'ops' }
  const mockStatus = (s: object) =>
    vi.mocked(api.get).mockImplementation(async () => s as never)

  afterEach(() => mockStatus(defaultStatus))

  it('shows Configured once saved, and no part of the key', async () => {
    mockStatus({ configured: true, api_key: 'sk-live-1234' })
    renderSection({ vendor: secretOnly })
    expect(await screen.findByText('Configured')).toBeTruthy()
    expect(document.body.textContent).not.toContain('sk-live-1234')
    expect(document.body.textContent).not.toContain('1234')
  })

  it('says 已配置 in Chinese', async () => {
    mockStatus({ configured: true, api_key: 'sk-live-1234' })
    renderSection({ vendor: secretOnly, lng: 'zh' })
    expect(await screen.findByText('已配置')).toBeTruthy()
  })

  it('keeps today\'s empty state when not configured', async () => {
    mockStatus({ configured: false })
    renderSection({ vendor: secretOnly })
    expect(await screen.findByText('No account configured')).toBeTruthy()
  })

  it('a vendor with non-secret fields still shows their values', async () => {
    mockStatus(defaultStatus)
    renderSection({ vendor: fakeVendor })
    expect(await screen.findByText('acme / ops')).toBeTruthy()
  })
})
