/**
 * Frontend tests for the cc-switch / external-config-override modal.
 *
 * Verifies:
 * - the type guard accepts the structured backend payload
 * - the modal renders in EN (default) and ZH and substitutes
 *   profile_id / settings_path into the i18n template
 * - the clear command is rendered verbatim (copy-paste must work)
 * - the "Use Default Profile" action fires the supplied callback
 */
import { render, screen, fireEvent } from '@testing-library/react'
import { describe, it, expect, vi } from 'vitest'
import { I18nextProvider, initReactI18next } from 'react-i18next'
import i18n from 'i18next'
import enTranslation from '../i18n/locales/en/translation.json'
import zhTranslation from '../i18n/locales/zh/translation.json'
import { ExternalConfigOverrideModal } from '../components/ExternalConfigOverrideModal'
import {
  isExternalConfigOverrideDetail,
  type ExternalConfigOverrideDetail,
} from '../components/externalConfigOverrideDetail'

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

const sampleDetail: ExternalConfigOverrideDetail = {
  code: 'external_config_override',
  profile_id: 'deepseek',
  overriding_keys: ['ANTHROPIC_BASE_URL', 'ANTHROPIC_AUTH_TOKEN'],
  settings_path: '/Users/example/.claude/settings.json',
  clear_command: 'python3 -c "import json,pathlib;..."',
  message: 'fallback english message',
}

describe('isExternalConfigOverrideDetail', () => {
  it('accepts the structured payload', () => {
    expect(isExternalConfigOverrideDetail(sampleDetail)).toBe(true)
  })

  it('rejects unrelated objects', () => {
    expect(isExternalConfigOverrideDetail(null)).toBe(false)
    expect(isExternalConfigOverrideDetail('Request failed (409)')).toBe(false)
    expect(
      isExternalConfigOverrideDetail({ code: 'something_else' }),
    ).toBe(false)
  })
})

describe('ExternalConfigOverrideModal — English', () => {
  it('renders the English title and substitutes profile_id', () => {
    render(
      <I18nextProvider i18n={makeI18n('en')}>
        <ExternalConfigOverrideModal
          detail={sampleDetail}
          onClose={() => {}}
        />
      </I18nextProvider>,
    )
    expect(
      screen.getByText(/External config conflicts/i),
    ).toBeInTheDocument()
    // profile_id and settings_path substituted into the template
    // (single text node, so a substring regex matches both)
    expect(
      screen.getByText(
        /Profile "deepseek" can't be used: \/Users\/example\/\.claude\/settings\.json/,
      ),
    ).toBeInTheDocument()
    // both overriding keys shown
    expect(screen.getByText('ANTHROPIC_BASE_URL')).toBeInTheDocument()
    expect(screen.getByText('ANTHROPIC_AUTH_TOKEN')).toBeInTheDocument()
  })

  it('renders the clear command verbatim', () => {
    render(
      <I18nextProvider i18n={makeI18n('en')}>
        <ExternalConfigOverrideModal
          detail={sampleDetail}
          onClose={() => {}}
        />
      </I18nextProvider>,
    )
    expect(
      screen.getByText(sampleDetail.clear_command),
    ).toBeInTheDocument()
  })
})

describe('ExternalConfigOverrideModal — Chinese', () => {
  it('renders in Chinese when i18n is set to zh', () => {
    render(
      <I18nextProvider i18n={makeI18n('zh')}>
        <ExternalConfigOverrideModal
          detail={sampleDetail}
          onUseDefault={() => {}}
          onClose={() => {}}
        />
      </I18nextProvider>,
    )
    // Chinese title — pinned so a future copy-edit doesn't accidentally
    // drop ZH localisation again.
    expect(
      screen.getByText(/外部工具的配置与当前 Profile 冲突/),
    ).toBeInTheDocument()
    expect(screen.getByText(/切换到默认 Profile/)).toBeInTheDocument()
  })
})

describe('ExternalConfigOverrideModal — actions', () => {
  it('calls onUseDefault when the default-profile button is clicked', () => {
    const onUseDefault = vi.fn()
    render(
      <I18nextProvider i18n={makeI18n('en')}>
        <ExternalConfigOverrideModal
          detail={sampleDetail}
          onUseDefault={onUseDefault}
          onClose={() => {}}
        />
      </I18nextProvider>,
    )
    fireEvent.click(screen.getByText(/Use Default Profile/i))
    expect(onUseDefault).toHaveBeenCalledTimes(1)
  })

  it('calls onClose when the Close button is clicked', () => {
    const onClose = vi.fn()
    render(
      <I18nextProvider i18n={makeI18n('en')}>
        <ExternalConfigOverrideModal
          detail={sampleDetail}
          onClose={onClose}
        />
      </I18nextProvider>,
    )
    fireEvent.click(screen.getByText('Close'))
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('hides the "Use Default" button when onUseDefault is omitted', () => {
    render(
      <I18nextProvider i18n={makeI18n('en')}>
        <ExternalConfigOverrideModal
          detail={sampleDetail}
          onClose={() => {}}
        />
      </I18nextProvider>,
    )
    expect(screen.queryByText(/Use Default Profile/i)).toBeNull()
  })
})
