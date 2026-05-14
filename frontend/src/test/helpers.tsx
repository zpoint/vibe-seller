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
import type { Task, ConversationItem, ConversationItemType, PlanVersion, TodoItem, TaskStep } from '../types'

// ── i18n test instance ─────────────────────────────────

const i18nTestInstance = i18n.createInstance()
i18nTestInstance.use(initReactI18next).init({
  resources: { en: { translation: enTranslation } },
  lng: 'en',
  fallbackLng: 'en',
  interpolation: { escapeValue: false },
})

// ── Factories ──────────────────────────────────────────

let _idCounter = 0

export function makeTask(overrides: Partial<Task> = {}): Task {
  return {
    id: `task-${++_idCounter}`,
    store_id: null,
    title: 'Test Task',
    description: null,
    status: 'pending',
    plan: null,
    result: null,
    todos: null,
    wait_condition: null,
    error: null,
    plan_mode: false,
    ai_profile_id: null,
    schedule_id: null,
    batch_id: null,
    created_at: new Date().toISOString(),
    started_at: null,
    completed_at: null,
    ...overrides,
  }
}

export function makePlan(overrides: Partial<PlanVersion> = {}): PlanVersion {
  return {
    version: 1,
    content: '## Plan\n1. Step one\n2. Step two',
    isCurrent: true,
    ...overrides,
  }
}

export function makeConversationItem(
  type: ConversationItemType,
  overrides: Partial<ConversationItem> = {},
): ConversationItem {
  const base: ConversationItem = {
    id: `item-${++_idCounter}`,
    type,
    timestamp: new Date().toISOString(),
  }

  if (type === 'plan') {
    base.plan = makePlan()
  } else if (type === 'user_message') {
    base.message = { role: 'user', content: 'Hello' }
  } else if (type === 'agent_message') {
    base.message = { role: 'assistant', content: 'Working on it...' }
  } else if (type === 'streaming') {
    base.message = { role: '_streaming', content: 'typing...' }
  } else if (type === 'result') {
    base.result = 'Task completed successfully'
  } else if (type === 'question') {
    base.questions = {
      request_id: 'q1',
      questions: [{ question: 'Which option?', options: [{ label: 'A' }, { label: 'B' }] }],
    }
  } else if (type === 'tool_call') {
    base.toolCall = { tool: 'Read', input: { file_path: 'app/models.py' } }
  } else if (type === 'thinking') {
    base.thinking = { content: 'Analyzing the code...', isStreaming: false }
  }

  return { ...base, ...overrides }
}

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
