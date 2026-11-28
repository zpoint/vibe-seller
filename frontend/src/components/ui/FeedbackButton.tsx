import { useTranslation } from 'react-i18next'

const FEEDBACK_EMAIL = 'zp0int@qq.com'

export function FeedbackButton({ version, platform, openExternal }: {
  version?: string
  platform?: string
  /** Test seam for the browser's mailto handler; production uses location.assign. */
  openExternal?: (href: string) => void
}) {
  const { t } = useTranslation()
  const body = [
    t('onboarding.feedbackBodyPrompt'),
    '',
    `${t('onboarding.version')}: ${version || t('onboarding.unknown')}`,
    `${t('onboarding.platform')}: ${platform || t('onboarding.unknown')}`,
    '',
  ].join('\n')
  const href = `mailto:${FEEDBACK_EMAIL}?subject=${encodeURIComponent(
    t('onboarding.feedbackSubject'),
  )}&body=${encodeURIComponent(body)}`

  const openMail = () => {
    try {
      if (openExternal) openExternal(href)
      else window.location.assign(href)
    } catch {
      window.prompt(t('onboarding.feedbackFallback'), body)
    }
  }

  return (
    <button
      type="button"
      data-testid="nav-feedback"
      data-email={FEEDBACK_EMAIL}
      onClick={openMail}
      title={t('onboarding.feedbackTitle')}
      className="p-1 text-gray-400 hover:text-indigo-600 whitespace-nowrap shrink-0"
    >
      <svg aria-hidden="true" className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
      </svg>
      <span className="sr-only">{t('onboarding.feedbackTitle')}</span>
    </button>
  )
}
