/**
 * The task error banner speaks the store's vendor: when the task's
 * store uses a REGISTRY vendor, the banner's sentence and help link
 * come from that vendor (the same vendor-first chain the settings
 * vendor panel uses, extracted into lib/vendorText) — an API-key-only
 * vendor never tells the user to add a saved login it does not have.
 * Non-vendor stores keep the shared category text and link exactly.
 *
 * Every case here has a negative control: rendering WITHOUT the vendor
 * prop is main's TaskErrorNote, and it must show the shared text.
 */
import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { I18nextProvider, initReactI18next } from 'react-i18next'
import i18n from 'i18next'
import enTranslation from '../i18n/locales/en/translation.json'
import zhTranslation from '../i18n/locales/zh/translation.json'
import { TaskErrorNote } from '../components/TaskErrorNote'
import type { BrowserVendor } from '../types'

vi.mock('../api', () => ({ api: { get: vi.fn(async () => []), post: vi.fn() } }))

function i18nFor(lng: 'en' | 'zh') {
  const inst = i18n.createInstance()
  inst.use(initReactI18next).init({
    resources: { en: { translation: enTranslation }, zh: { translation: zhTranslation } },
    lng, fallbackLng: 'en', interpolation: { escapeValue: false },
  })
  return inst
}

/** A registry vendor shaped like an API-key-only one: no credential
 *  fields, its own LoginRequired override, and an errors docs page. */
const apiKeyVendor: BrowserVendor = {
  key: 'acmebrowser',
  display_name_en: 'Acme Browser',
  display_name_zh: 'Acme 浏览器',
  kind: 'antidetect',
  platforms: ['windows'],
  selectable: true,
  account_cardinality: 'single',
  credential_fields: [],
  lists_profiles: true,
  profiles_scope: 'account',
  picker_kind: 'generic',
  i18n_prefix: 'vendors.acmebrowser.',
  docs: {
    errors: {
      topic: 'acmebrowserErrors',
      zh: 'https://docs.vibe-sellers.com/acmebrowser/#errors-zh',
      en: 'https://docs.vibe-sellers.com/en/acmebrowser/#errors-en',
    },
  },
}

/** The same vendor with NO own key for the category — the shared
 *  browserVendor.* text with the display name interpolated. */
const noOverrideVendor: BrowserVendor = {
  ...apiKeyVendor,
  key: 'kaaaaaaa1browser',
  display_name_en: 'Kaaaaaaa1 Browser',
  display_name_zh: 'Kaaaaaaa1 浏览器',
  i18n_prefix: 'vendors.kaaaaaaa1.',
  docs: {},
}

const en = { translation: { ...enTranslation } }
// The fixture vendors' own overrides — the shape a vendor's spec
// contributes to the translation tree (placeholder names only).
;(en.translation as Record<string, unknown>).vendors = {
  acmebrowser: {
    LoginRequired: 'Acme Browser uses an API key — check the key in the client settings.',
  },
}
const i18nEn = i18n.createInstance()
i18nEn.use(initReactI18next).init({
  resources: { en }, lng: 'en', fallbackLng: 'en',
  interpolation: { escapeValue: false },
})

const renderNote = (props: {
  error: string
  category?: string | null
  vendor?: BrowserVendor | null
}) => render(
  <I18nextProvider i18n={i18nEn}>
    <TaskErrorNote {...props} />
  </I18nextProvider>,
)

const note = () => screen.getByTestId('task-error')
const helpLink = () => screen.getByTestId('docs-help-link') as HTMLAnchorElement

describe('TaskErrorNote speaks the store vendor', () => {
  it('(a) a registry vendor with its own key: the vendor text + the vendor errors docs link', () => {
    renderNote({
      error: '[LOGIN_REQUIRED] Log into the client on this machine, then retry / refresh.',
      category: 'login_required',
      vendor: apiKeyVendor,
    })
    expect(note().textContent).toContain(
      'Acme Browser uses an API key — check the key in the client settings.'
    )
    // The shared login_required sentence must NOT be there.
    expect(note().textContent).not.toContain('no saved login exists')
    expect(helpLink().href).toBe(
      'https://docs.vibe-sellers.com/en/acmebrowser/#errors-en'
    )
  })

  it('(a) negative control: without the vendor prop (main) the shared text + shared link show', () => {
    renderNote({
      error: '[LOGIN_REQUIRED] Log into the client on this machine, then retry / refresh.',
      category: 'login_required',
    })
    expect(note().textContent).toContain(
      enTranslation.tasks.error_login_required
    )
    expect(note().textContent).not.toContain('Acme Browser uses an API key')
    expect(helpLink().dataset.topic).toBe('browserLoginRequired')
  })

  it('(b) a vendor with no own key: the shared browserVendor text with the display name', () => {
    renderNote({
      error: '[CLIENT_NOT_RUNNING] Start the browser client, or check its install path.',
      category: 'client_not_running',
      vendor: noOverrideVendor,
    })
    // browserVendor.ClientNotRunning with {{vendor}} interpolated —
    // main's banner shows the tasks.error_client_not_running sentence
    // instead (the negative control below).
    const shared = enTranslation.browserVendor.ClientNotRunning
    expect(note().textContent).toContain(
      shared.replace('{{vendor}}', 'Kaaaaaaa1 Browser')
    )
    // No vendor docs entry → the shared topic link, as before.
    expect(helpLink().dataset.topic).toBe('browserClientNotRunning')
  })

  it('(b) negative control: without the vendor prop the generic category sentence shows', () => {
    renderNote({
      error: '[CLIENT_NOT_RUNNING] Start the browser client, or check its install path.',
      category: 'client_not_running',
    })
    expect(note().textContent).toContain(
      enTranslation.tasks.error_client_not_running
    )
    expect(note().textContent).not.toContain('Kaaaaaaa1 Browser')
  })

  it('(c) a non-vendor store keeps today\'s generic text and link byte-identically', () => {
    const { container } = render(
      <I18nextProvider i18n={i18nFor('en')}>
        <TaskErrorNote
          error='[LOGIN_REQUIRED] Log into the client on this machine, then retry / refresh.'
          category='login_required'
        />
      </I18nextProvider>,
    )
    expect(container.textContent).toContain(
      enTranslation.tasks.error_login_required
    )
    expect(helpLink().dataset.topic).toBe('browserLoginRequired')
    expect(helpLink().href).toBe(
      'https://docs.vibe-sellers.com/en/browsers/#error-login-required'
    )
    expect(container.textContent).not.toContain('vendor')
  })
})
