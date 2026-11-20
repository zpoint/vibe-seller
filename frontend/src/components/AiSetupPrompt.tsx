// "Set up AI first": shown instead of the new-task form when no AI the
// task could run on is set up (see lib/aiSetup.ts). Two ways forward —
// one click to the AI settings page (where AiSetupGuide opens), or the
// setup tutorial in a new tab. Same card look as OnboardingGuide.
import { useEffect } from 'react'
import { createPortal } from 'react-dom'
import { useTranslation } from 'react-i18next'
import { aiDocsUrl } from '../lib/aiSetup'
import { sendEvent } from '../lib/telemetry'
import { FrontendEvent } from '../lib/telemetryEvents'

export type AiPromptChoice = 'settings' | 'docs' | 'later'

export function AiSetupPrompt({ onChoose }: {
  /** 'settings' / 'later' close the dialog; 'docs' keeps it open. */
  onChoose: (choice: AiPromptChoice) => void
}) {
  const { t, i18n } = useTranslation()

  useEffect(() => { sendEvent(FrontendEvent.AI_SETUP_PROMPT_SHOWN, {}) }, [])

  const choose = (choice: AiPromptChoice) => {
    sendEvent(FrontendEvent.AI_SETUP_PROMPT_CHOICE, { choice })
    onChoose(choice)
  }

  return createPortal(
    <div
      data-testid="ai-setup-prompt"
      className="fixed inset-0 z-[70] flex items-center justify-center bg-black/50 p-4"
      onClick={e => { if (e.target === e.currentTarget) choose('later') }}
      onKeyDown={e => { if (e.key === 'Escape') choose('later') }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="ai-setup-prompt-title"
        className="bg-white rounded-xl shadow-2xl border border-gray-200 p-6 w-[min(92vw,480px)]"
      >
        <div className="flex items-start justify-between gap-3 mb-2">
          <h2 id="ai-setup-prompt-title" className="text-lg font-semibold text-gray-900">
            {t('aiSetup.prompt.title')}
          </h2>
          <button
            onClick={() => choose('later')}
            aria-label={t('common.close')}
            className="p-1 text-gray-400 hover:text-gray-600"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" /></svg>
          </button>
        </div>
        <p className="text-base leading-relaxed text-gray-600">{t('aiSetup.prompt.body')}</p>
        <p className="text-base leading-relaxed text-gray-600 mt-2">{t('aiSetup.prompt.time')}</p>
        <div className="flex flex-wrap items-center justify-between gap-2 mt-5">
          <button onClick={() => choose('later')} className="px-3 py-2 text-sm text-gray-500 hover:text-gray-700">
            {t('aiSetup.prompt.gotIt')}
          </button>
          <div className="flex flex-wrap items-center gap-2">
            <a
              href={aiDocsUrl(i18n.language)}
              target="_blank"
              rel="noopener noreferrer"
              onClick={() => choose('docs')}
              className="px-4 py-2 text-sm rounded-lg border border-gray-200 text-gray-600 hover:bg-gray-50"
            >
              {t('aiSetup.prompt.docs')} ↗
            </a>
            <button
              autoFocus
              onClick={() => choose('settings')}
              className="px-4 py-2 text-sm rounded-lg bg-indigo-600 text-white hover:bg-indigo-700"
            >
              {t('aiSetup.prompt.goToSettings')}
            </button>
          </div>
        </div>
      </div>
    </div>,
    document.body,
  )
}
