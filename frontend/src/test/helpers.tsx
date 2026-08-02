/**
 * Shared test factories and render helpers for conversation tests.
 */
import { render } from '@testing-library/react'
import { I18nextProvider } from 'react-i18next'
import i18n from 'i18next'
import { initReactI18next } from 'react-i18next'
import { createRef } from 'react'
import enTranslation from '../i18n/locales/en/translation.json'
import { ConversationStream } from '../components/conversation/ConversationStream'
import type { Task, ConversationItem, TodoItem, TaskStep } from '../types'
import { makeTask } from './factories'

// ── i18n test instance ─────────────────────────────────

const i18nTestInstance = i18n.createInstance()
i18nTestInstance.use(initReactI18next).init({
  resources: { en: { translation: enTranslation } },
  lng: 'en',
  fallbackLng: 'en',
  interpolation: { escapeValue: false },
})

// ── Factories ──────────────────────────────────────────
//
// Defined in `./factories` (no rendering imports, so hook tests can use
// them too) and re-exported here for the render-helper callers.

export { makeTask, makePlan, makeConversationItem } from './factories'

// ── ConversationStream render helper ───────────────────

interface ConversationStreamOverrides {
  items?: ConversationItem[]
  todoItems?: TodoItem[]
  task?: Partial<Task>
  steps?: TaskStep[]
  screenshots?: Record<string, string>
  pendingQuestions?: { request_id: string; questions: { header?: string; question: string; options?: { label: string; description?: string }[] }[] } | null
  selectedAnswers?: Record<string, string>
  otherInputs?: Record<string, string>
  showOtherInput?: Record<string, boolean>
  onSelectAnswer?: (q: string, a: string) => void
  onToggleOther?: (q: string) => void
  onSetOtherAnswer?: (q: string, t: string) => void
  onSubmitAll?: () => void
  onConfirmPlan?: () => void
  onRequestChanges?: () => void
  isActive?: boolean
}

export function renderConversationStream(overrides: ConversationStreamOverrides = {}) {
  const task = makeTask({ status: 'running', ...overrides.task })
  const questionBannerRef = createRef<HTMLDivElement>()

  return render(
    <I18nextProvider i18n={i18nTestInstance}>
      <ConversationStream
        items={overrides.items ?? []}
        todoItems={overrides.todoItems ?? []}
        task={task}
        steps={overrides.steps ?? []}
        screenshots={overrides.screenshots ?? {}}
        pendingQuestions={overrides.pendingQuestions ?? null}
        selectedAnswers={overrides.selectedAnswers ?? {}}
        otherInputs={overrides.otherInputs ?? {}}
        showOtherInput={overrides.showOtherInput ?? {}}
        onSelectAnswer={overrides.onSelectAnswer ?? (() => {})}
        onToggleOther={overrides.onToggleOther ?? (() => {})}
        onSetOtherAnswer={overrides.onSetOtherAnswer ?? (() => {})}
        onSubmitAll={overrides.onSubmitAll ?? (() => {})}
        onConfirmPlan={overrides.onConfirmPlan ?? (() => {})}
        onRequestChanges={overrides.onRequestChanges}
        questionBannerRef={questionBannerRef}
        isActive={overrides.isActive ?? true}
      />
    </I18nextProvider>,
  )
}

export { i18nTestInstance }
