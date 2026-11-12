import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';
import enTranslation from './locales/en/translation.json';
import zhTranslation from './locales/zh/translation.json';

// Mainland-China OS time zones. `PRC` is what some Windows builds
// report instead of Asia/Shanghai.
const CN_TIMEZONES = new Set([
  'Asia/Shanghai',
  'Asia/Chongqing',
  'Asia/Chungking',
  'Asia/Harbin',
  'Asia/Urumqi',
  'Asia/Kashgar',
  'PRC',
]);

/**
 * Pure initial-language decision (exported for unit tests):
 *  1. an explicit saved choice (en/zh) wins;
 *  2. ANY entry in the browser's language list starting with zh -> zh;
 *  3. a mainland-China OS time zone -> zh;
 *  4. otherwise en.
 *
 * Why the time zone: `navigator.languages` reports the browser's own
 * language list, not the OS display language — a Chinese Windows with
 * an English browser lists en-US first and would land in English. The
 * time zone comes from the OS, so it catches that case. This product's
 * users are mainly China-based sellers; anyone who prefers English
 * switches once and the choice is saved (per origin).
 */
export function detectLanguage(opts: {
  saved: string | null;
  languages: readonly string[];
  timeZone: string;
}): 'en' | 'zh' {
  if (opts.saved === 'en' || opts.saved === 'zh') {
    return opts.saved;
  }
  if (opts.languages.some((l) => l.toLowerCase().startsWith('zh'))) {
    return 'zh';
  }
  if (CN_TIMEZONES.has(opts.timeZone)) {
    return 'zh';
  }
  return 'en';
}

const getInitialLanguage = (): 'en' | 'zh' => {
  let saved: string | null = null;
  let languages: readonly string[] = [];
  let timeZone = '';
  if (typeof window !== 'undefined') {
    saved = localStorage.getItem('vibe-seller-language');
    languages = navigator.languages ?? [navigator.language || ''];
    try {
      timeZone = Intl.DateTimeFormat().resolvedOptions().timeZone;
    } catch {
      timeZone = '';
    }
  }
  return detectLanguage({ saved, languages, timeZone });
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
    lng: getInitialLanguage(),
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
