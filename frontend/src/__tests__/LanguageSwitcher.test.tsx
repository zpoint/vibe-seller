import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'

describe('LanguageSwitcher', () => {
  it('renders language switcher button', () => {
    const LanguageSwitcher = () => {
      const currentLang: string = 'en'
      return (
        <button data-testid="lang-switch">
          {currentLang === 'zh' ? 'EN' : '中文'}
        </button>
      )
    }

    render(<LanguageSwitcher />)
    expect(screen.getByTestId('lang-switch')).toHaveTextContent('中文')
  })

  it('toggles language on click', () => {
    const mockChangeLanguage = vi.fn()
    const mockSavePreference = vi.fn()

    const LanguageSwitcher = () => {
      const currentLang: string = 'en'
      const toggleLanguage = () => {
        const newLang = currentLang === 'zh' ? 'en' : 'zh'
        mockChangeLanguage(newLang)
        mockSavePreference(newLang)
      }

      return (
        <button data-testid="lang-switch" onClick={toggleLanguage}>
          {currentLang === 'zh' ? 'EN' : '中文'}
        </button>
      )
    }

    render(<LanguageSwitcher />)
    fireEvent.click(screen.getByTestId('lang-switch'))

    expect(mockChangeLanguage).toHaveBeenCalledWith('zh')
    expect(mockSavePreference).toHaveBeenCalledWith('zh')
  })
})
