/**
 * The restart-needed notice: shown while a synced skill's gate code is
 * waiting on a restart, hidden otherwise — in both languages. Gates
 * load once per process, so without this the user has no way to know
 * the change is not live yet.
 */
import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { I18nextProvider, initReactI18next } from 'react-i18next'
import i18n from 'i18next'
import enTranslation from '../i18n/locales/en/translation.json'
import zhTranslation from '../i18n/locales/zh/translation.json'
import { RestartNeededNotice } from '../components/RestartNeededNotice'

const make = (lng: 'en' | 'zh' = 'en') => {
  const instance = i18n.createInstance()
  instance.use(initReactI18next).init({
    lng,
    resources: {
      en: { translation: enTranslation },
      zh: { translation: zhTranslation },
    },
    interpolation: { escapeValue: false },
  })
  return instance
}

const renderNotice = (
  needsRestart: boolean | null | undefined,
  lng: 'en' | 'zh' = 'en',
) =>
  render(
    <I18nextProvider i18n={make(lng)}>
      <RestartNeededNotice needsRestart={needsRestart} />
    </I18nextProvider>,
  )

describe('restart-needed notice', () => {
  it('shows when a synced skill changed gate code', () => {
    renderNotice(true)
    expect(screen.getByTestId('restart-needed')).toBeTruthy()
  })

  it('stays hidden when nothing is waiting on a restart', () => {
    renderNotice(false)
    expect(screen.queryByTestId('restart-needed')).toBeNull()
    renderNotice(null)
    expect(screen.queryByTestId('restart-needed')).toBeNull()
    renderNotice(undefined)
    expect(screen.queryByTestId('restart-needed')).toBeNull()
  })

  it('has a Chinese string too', () => {
    renderNotice(true, 'zh')
    expect(screen.getByTestId('restart-needed').textContent).toContain('重启')
  })
})
