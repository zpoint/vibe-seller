/**
 * Frontend tests for the inline ``external_config_override`` task
 * error card. This is what users see on a failed task — distinct
 * from the modal that pops on the Settings page (same i18n keys,
 * different layout).
 */
import { render, screen } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import { I18nextProvider, initReactI18next } from 'react-i18next'
import i18n from 'i18next'
import enTranslation from '../i18n/locales/en/translation.json'
import zhTranslation from '../i18n/locales/zh/translation.json'
import { ExternalConfigOverrideErrorCard } from '../components/ExternalConfigOverrideErrorCard'

const makeI18n = (lng: 'en' | 'zh') => {
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

const structuredJson = JSON.stringify({
  code: 'external_config_override',
  profile_id: 'deepseek',
  overriding_keys: ['ANTHROPIC_BASE_URL', 'ANTHROPIC_AUTH_TOKEN'],
  settings_path: '/Users/example/.claude/settings.json',
  clear_command: 'python3 -c "import json,pathlib;..."',
  message: 'fallback english message',
})

describe('ExternalConfigOverrideErrorCard — structured JSON', () => {
  it('renders i18n title and substitutes params in English', () => {
    render(
      <I18nextProvider i18n={makeI18n('en')}>
        <ExternalConfigOverrideErrorCard error={structuredJson} />
      </I18nextProvider>,
    )
    expect(
      screen.getByText(/External config conflicts/i),
    ).toBeInTheDocument()
    expect(
      screen.getByText(
        /Profile "deepseek" can't be used: \/Users\/example\/\.claude\/settings\.json/,
      ),
    ).toBeInTheDocument()
    expect(screen.getByText('ANTHROPIC_BASE_URL')).toBeInTheDocument()
  })

  it('renders the same content in Chinese when locale is zh', () => {
    render(
      <I18nextProvider i18n={makeI18n('zh')}>
        <ExternalConfigOverrideErrorCard error={structuredJson} />
      </I18nextProvider>,
    )
    expect(
      screen.getByText(/外部工具的配置与当前 Profile 冲突/),
    ).toBeInTheDocument()
    // The user-supplied params are not translated — they appear
    // verbatim inside the localised template.
    expect(
      screen.getByText(/Profile「deepseek」无法使用/),
    ).toBeInTheDocument()
  })

  it('renders the cleanup command verbatim', () => {
    render(
      <I18nextProvider i18n={makeI18n('en')}>
        <ExternalConfigOverrideErrorCard error={structuredJson} />
      </I18nextProvider>,
    )
    expect(
      screen.getByText('python3 -c "import json,pathlib;..."'),
    ).toBeInTheDocument()
  })
})

describe('ExternalConfigOverrideErrorCard — defensive fallback', () => {
  it('renders the raw string when the error is not JSON', () => {
    const legacy = 'Some old task error message that is not JSON.'
    render(
      <I18nextProvider i18n={makeI18n('en')}>
        <ExternalConfigOverrideErrorCard error={legacy} />
      </I18nextProvider>,
    )
    expect(screen.getByText(legacy)).toBeInTheDocument()
    // No i18n title rendered for the legacy case (we don't have
    // enough structure to fill the template).
    expect(
      screen.queryByText(/External config conflicts/i),
    ).toBeNull()
  })

  it('renders the raw string when JSON is not the expected shape', () => {
    const wrongShape = JSON.stringify({ message: 'something else' })
    render(
      <I18nextProvider i18n={makeI18n('en')}>
        <ExternalConfigOverrideErrorCard error={wrongShape} />
      </I18nextProvider>,
    )
    expect(screen.getByText(wrongShape)).toBeInTheDocument()
  })
})
