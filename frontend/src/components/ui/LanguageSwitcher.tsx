import { useTranslation } from 'react-i18next'
import { saveLanguagePreference } from '../../i18n'
import { sendEvent } from '../../lib/telemetry'
import { FrontendEvent } from '../../lib/telemetryEvents'

export function LanguageSwitcher() {
  const { i18n, t } = useTranslation()
  const currentLang = i18n.language

  const toggleLanguage = () => {
    const newLang = currentLang === 'zh' ? 'en' : 'zh'
    sendEvent(FrontendEvent.LANGUAGE_SWITCHED, { to_language: newLang })
    i18n.changeLanguage(newLang)
    saveLanguagePreference(newLang)
  }

  return (
    <button
      onClick={toggleLanguage}
      className="px-2 py-1 text-xs font-medium text-gray-600 hover:text-gray-900 bg-gray-100 hover:bg-gray-200 rounded transition-colors"
      title={t('language.title')}
    >
      {currentLang === 'zh' ? 'EN' : '中文'}
    </button>
  )
}
