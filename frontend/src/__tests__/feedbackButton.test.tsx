import { describe, expect, it, vi, afterEach } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { I18nextProvider, initReactI18next } from 'react-i18next'
import i18n from 'i18next'

import { bootApp } from '../test/appHarness'

import { FeedbackButton } from '../components/ui/FeedbackButton'
import en from '../i18n/locales/en/translation.json'
import zh from '../i18n/locales/zh/translation.json'

function i18nFor(lng: 'en' | 'zh') {
  const inst = i18n.createInstance()
  inst.use(initReactI18next).init({
    resources: { en: { translation: en }, zh: { translation: zh } },
    lng, fallbackLng: 'en', interpolation: { escapeValue: false },
  })
  return inst
}

function renderButton(lng: 'en' | 'zh', openExternal: (href: string) => void) {
  return render(
    <I18nextProvider i18n={i18nFor(lng)}>
      <FeedbackButton version="9d8a5ba7" platform="mac" openExternal={openExternal} />
    </I18nextProvider>,
  )
}

describe('feedback button', () => {
  it('opens a mailto with version and platform in the body', () => {
    const openExternal = vi.fn()
    const prompt = vi.spyOn(window, 'prompt').mockReturnValue(null)
    renderButton('en', openExternal)
    fireEvent.click(screen.getByRole('button', { name: 'Contact us' }))
    const href = decodeURIComponent(openExternal.mock.calls[0][0])
    expect(href).toContain('mailto:zp0int@qq.com')
    expect(href).toContain('Vibe Seller feedback')
    expect(href).toContain('Version: 9d8a5ba7')
    expect(href).toContain('Platform: mac')
    expect(href).toContain('Describe your issue')
    expect(prompt).not.toHaveBeenCalled()
  })

  it('shows the email and version body when the mail client fails', () => {
    const openExternal = vi.fn(() => {
      throw new Error('no mail handler')
    })
    const prompt = vi.spyOn(window, 'prompt').mockReturnValue(null)
    renderButton('en', openExternal)
    fireEvent.click(screen.getByRole('button', { name: 'Contact us' }))
    expect(prompt).toHaveBeenCalledTimes(1)
    expect(prompt.mock.calls[0][0]).toContain('zp0int@qq.com')
    expect(prompt.mock.calls[0][1]).toContain('Version: 9d8a5ba7')
    expect(prompt.mock.calls[0][1]).toContain('Platform: mac')
  })

  it('uses the Chinese feedback subject, labels and body prompt', () => {
    const openExternal = vi.fn()
    renderButton('zh', openExternal)
    fireEvent.click(screen.getByRole('button', { name: '意见反馈' }))
    const href = decodeURIComponent(openExternal.mock.calls[0][0])
    expect(href).toContain('意见反馈')
    expect(href).toContain('请描述你遇到的问题')
    expect(href).toContain('版本: 9d8a5ba7')
    expect(href).toContain('平台: mac')
  })

  it('places the feedback button in the top navigation', async () => {
    await bootApp({ guideSeen: true })
    expect(screen.getByTestId('nav-feedback')).toBeInTheDocument()
  })
})

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})
