import { afterEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, screen, waitFor } from '@testing-library/react'

import { bootApp } from '../test/appHarness'
import type { BrowserVendor } from '../types'
import en from '../i18n/locales/en/translation.json'
import zh from '../i18n/locales/zh/translation.json'

vi.mock('../lib/telemetry', () => ({
  sendEvent: vi.fn(), lengthBucket: () => 'small', ageBucket: () => 'new',
  registerUserHash: vi.fn(), unregisterUserHash: vi.fn(),
}))

const link = (topic: string, zhUrl: string, enUrl: string) => ({
  topic, zh: zhUrl, en: enUrl,
})
const vendor = (
  key: string,
  kind: 'plain' | 'antidetect',
  docs: BrowserVendor['docs'],
): BrowserVendor => ({
  key, display_name_en: key, display_name_zh: key, kind,
  platforms: ['windows', 'wsl', 'mac', 'linux'], selectable: true,
  account_cardinality: kind === 'plain' ? 'none' : 'multi',
  credential_fields: [], lists_profiles: false, profiles_scope: 'login',
  picker_kind: kind === 'plain' ? 'none' : 'custom', i18n_prefix: 'settings',
  docs,
})

const VENDORS = [
  vendor('chrome', 'plain', {
    setup: link(
      'chromeSetup',
      'https://docs.vibe-sellers.com/stores/#新建店铺',
      'https://docs.vibe-sellers.com/en/stores/#creating-a-store',
    ),
  }),
  vendor('ziniao', 'antidetect', {
    setup: link(
      'ziniaoSetup',
      'https://docs.vibe-sellers.com/ziniao/',
      'https://docs.vibe-sellers.com/en/ziniao/',
    ),
  }),
  vendor('wenmai', 'antidetect', {
    setup: link(
      'wenmaiSetup',
      'https://docs.vibe-sellers.com/wenmai/',
      'https://docs.vibe-sellers.com/en/wenmai/',
    ),
  }),
]

async function openStoreForm() {
  await bootApp({
    guideSeen: true,
    route: url => url === '/api/browser-vendors' ? VENDORS : undefined,
  })
  fireEvent.click(await screen.findByRole('button', { name: '+ New Store' }))
  const picker = await screen.findByRole('combobox', { name: en.tasks.browserBackend })
  await waitFor(() => expect(picker.children).toHaveLength(VENDORS.length))
  return picker
}

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('create-store setup guides', () => {
  it('shows the selected browser guide and switches it with the backend', async () => {
    const picker = await openStoreForm()
    const guide = screen.getByTestId('docs-help-link') as HTMLAnchorElement
    expect(guide.textContent).toContain(en.settings.browserSetupGuide)
    expect(guide.href).toBe(new URL(VENDORS[0].docs.setup.en).href)

    fireEvent.change(picker, { target: { value: 'ziniao' } })
    expect(guide.href).toBe(new URL(VENDORS[1].docs.setup.en).href)
    fireEvent.change(picker, { target: { value: 'wenmai' } })
    expect(guide.href).toBe(new URL(VENDORS[2].docs.setup.en).href)
  })

  it('uses the Chinese guide label and URL after switching the UI language', async () => {
    await openStoreForm()
    fireEvent.click(screen.getByRole('button', { name: '中文' }))
    const guide = screen.getByTestId('docs-help-link') as HTMLAnchorElement
    await waitFor(() => {
      expect(guide.textContent).toContain(zh.settings.browserSetupGuide)
      expect(guide.href).toBe(new URL(VENDORS[0].docs.setup.zh).href)
    })
  })
})
