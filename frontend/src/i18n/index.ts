import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';
import enTranslation from './locales/en/translation.json';
import zhTranslation from './locales/zh/translation.json';

// Resolve the initial language: an explicit saved choice wins;
// otherwise auto-detect from the browser/OS language (so a Chinese
// system opens straight into Chinese). Falls back to English.
const getSavedLanguage = (): string => {
  if (typeof window !== 'undefined') {
    const saved = localStorage.getItem('vibe-seller-language');
    if (saved === 'en' || saved === 'zh') {
      return saved;
    }
    const nav = (navigator.language || '').toLowerCase();
    if (nav.startsWith('zh')) {
      return 'zh';
    }
  }
  return 'en';
};

const resources = {
  en: {
    translation: enTranslation,
  },
  zh: {
    translation: zhTranslation,
  },
};

i18n
  .use(initReactI18next)
  .init({
    resources,
    lng: getSavedLanguage(),
    fallbackLng: 'en',
    interpolation: {
      escapeValue: false,
    },
  });

// Fire-and-forget: tell the backend which locale this install is
// rendering in. The backend forwards to PostHog (anonymous,
// install-level) so the telemetry dashboard can split adoption by
// language. Failure is silent — telemetry is non-essential and the
// endpoint also no-ops when VIBE_SELLER_TELEMETRY=0.
const reportLocale = (lang: string): void => {
  if (typeof fetch === 'undefined') return;
  fetch('/api/telemetry/locale', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ locale: lang }),
  }).catch(() => {});
};

reportLocale(i18n.language);

export default i18n;

// Helper function to save language preference
export const saveLanguagePreference = (lang: string): void => {
  if (typeof window !== 'undefined') {
    localStorage.setItem('vibe-seller-language', lang);
  }
  reportLocale(lang);
};
