/**
 * Contract tests for the Settings tab shape after the redesign:
 *   - tabs: stores, general, aiAgent, email, account, integrations
 *   - tab_security renamed to tab_account (login settings + users)
 *   - tab_channels renamed to tab_email (email accounts)
 *   - new tab_general for cross-cutting defaults (task retention,
 *     concurrency, schedule defaults, telemetry, default plan mode)
 *   - eventSync tab still removed; its content lives in Integrations
 */
import { describe, it, expect } from 'vitest'
import fs from 'fs'
import path from 'path'
import enTranslation from '../i18n/locales/en/translation.json'
import zhTranslation from '../i18n/locales/zh/translation.json'

const settingsSource = fs.readFileSync(
  path.resolve(__dirname, '../views/SettingsView.tsx'),
  'utf-8',
)
const integrationsPanelSource = fs.readFileSync(
  path.resolve(
    __dirname,
    '../components/settings/IntegrationsPanel.tsx',
  ),
  'utf-8',
)
const appSource = fs.readFileSync(
  path.resolve(__dirname, '../App.tsx'),
  'utf-8',
)

type SettingsTranslations = { settings: Record<string, string> }

describe('Settings tab redesign', () => {
  describe('translations', () => {
    it('EN: new tab keys are present', () => {
      const s = (enTranslation as SettingsTranslations).settings
      expect(s.tab_stores).toBeTruthy()
      expect(s.tab_general).toBeTruthy()
      expect(s.tab_aiAgent).toBeTruthy()
      expect(s.tab_email).toBeTruthy()
      expect(s.tab_account).toBeTruthy()
      expect(s.tab_integrations).toBeTruthy()
    })

    it('ZH: new tab keys are present', () => {
      const s = (zhTranslation as SettingsTranslations).settings
      expect(s.tab_stores).toBeTruthy()
      expect(s.tab_general).toBeTruthy()
      expect(s.tab_aiAgent).toBeTruthy()
      expect(s.tab_email).toBeTruthy()
      expect(s.tab_account).toBeTruthy()
      expect(s.tab_integrations).toBeTruthy()
    })

    it('EN: legacy keys are removed', () => {
      const s = (enTranslation as SettingsTranslations).settings
      expect('tab_security' in s).toBe(false)
      expect('tab_channels' in s).toBe(false)
      expect('tab_eventSync' in s).toBe(false)
    })

    it('ZH: legacy keys are removed', () => {
      const s = (zhTranslation as SettingsTranslations).settings
      expect('tab_security' in s).toBe(false)
      expect('tab_channels' in s).toBe(false)
      expect('tab_eventSync' in s).toBe(false)
    })
  })

  describe('SettingsView source', () => {
    it('tab list contains new tabs and excludes legacy ones', () => {
      const match = settingsSource.match(
        /SettingsTab\[\]\s*=\s*\[([^\]]*)\]/,
      )
      expect(match, 'tab array not found').toBeTruthy()
      const tabList = match![1]
      expect(tabList).toMatch(/['"]general['"]/)
      expect(tabList).toMatch(/['"]account['"]/)
      expect(tabList).toMatch(/['"]email['"]/)
      expect(tabList).not.toMatch(/['"]security['"]/)
      expect(tabList).not.toMatch(/['"]channels['"]/)
      expect(tabList).not.toMatch(/['"]eventSync['"]/)
    })

    it('does not branch on legacy tab keys', () => {
      expect(settingsSource).not.toMatch(/settingsTab\s*===\s*['"]eventSync['"]/)
      expect(settingsSource).not.toMatch(/settingsTab\s*===\s*['"]security['"]/)
      expect(settingsSource).not.toMatch(/settingsTab\s*===\s*['"]channels['"]/)
    })

    it('IntegrationsPanel still renders Dida365Panel + WeCom', () => {
      expect(integrationsPanelSource).toMatch(/<Dida365Panel\s*\/>/)
      expect(integrationsPanelSource).toMatch(/<WeComBotSection\s*\/>/)
    })
  })

  describe('App.tsx state type', () => {
    it('settingsTab uses shared SettingsTab type', () => {
      // App.tsx and SettingsView.tsx must not duplicate the union;
      // App imports the canonical SettingsTab type and references it.
      expect(appSource).toMatch(
        /import\s*{[^}]*\btype\s+SettingsTab\b[^}]*}\s*from\s*['"]\.\/views\/SettingsView['"]/,
      )
      expect(appSource).toMatch(/useState<SettingsTab>/)
      // No legacy keys leak into App.tsx
      expect(appSource).not.toMatch(/useState<[^>]*['"]security['"]/)
      expect(appSource).not.toMatch(/useState<[^>]*['"]channels['"]/)
    })
  })
})
