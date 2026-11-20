/**
 * The New Task prompt's example text follows the UI language: a
 * Chinese UI never shows an English example (and vice versa), in both
 * scopes (all stores / one store).
 */
import { render, screen } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import { I18nextProvider, initReactI18next } from 'react-i18next'
import i18n from 'i18next'
import en from '../i18n/locales/en/translation.json'
import zh from '../i18n/locales/zh/translation.json'
import { CreateTaskModal } from '../components/CreateTaskModal'
import type { Store } from '../types'

function renderIn(lng: 'en' | 'zh', showAllTasks: boolean) {
  const inst = i18n.createInstance()
  inst.use(initReactI18next).init({
    resources: { en: { translation: en }, zh: { translation: zh } },
    lng,
    fallbackLng: 'en',
    interpolation: { escapeValue: false },
  })
  const store = showAllTasks ? null : ({ id: 's1', name: 'Shop' } as Store)
  render(
    <I18nextProvider i18n={inst}>
      <CreateTaskModal
        showAllTasks={showAllTasks}
        storeName={store?.name}
        selectedStore={store}
        onClose={() => {}}
        onSubmit={async () => {}}
      />
    </I18nextProvider>,
  )
  return screen.getByRole('textbox').getAttribute('placeholder')
}

describe('New Task prompt example', () => {
  it.each([
    ['zh', true, zh.tasks.promptPlaceholderAll],
    ['zh', false, zh.tasks.promptPlaceholderStore],
    ['en', true, en.tasks.promptPlaceholderAll],
    ['en', false, en.tasks.promptPlaceholderStore],
  ] as const)('%s, all stores=%s', (lng, all, expected) => {
    expect(renderIn(lng, all)).toBe(expected)
  })

  it('the Chinese examples are Chinese', () => {
    expect(zh.tasks.promptPlaceholderAll).toMatch(/^例如/)
    expect(zh.tasks.promptPlaceholderStore).toMatch(/^例如/)
  })
})
