/**
 * The install id under the version: short on screen, whole on copy.
 */
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { I18nextProvider, initReactI18next } from 'react-i18next'
import i18n from 'i18next'
import enTranslation from '../i18n/locales/en/translation.json'
import { InstallIdBadge } from '../components/InstallIdBadge'

const i18nTestInstance = i18n.createInstance()
i18nTestInstance.use(initReactI18next).init({
  resources: { en: { translation: enTranslation } },
  lng: 'en',
  fallbackLng: 'en',
  interpolation: { escapeValue: false },
})

const ID = '3f2b9c1e-7a4d-4e8f-9b61-0c5d2e7f8a90'

function renderBadge(installId: string) {
  return render(
    <I18nextProvider i18n={i18nTestInstance}>
      <InstallIdBadge installId={installId} />
    </I18nextProvider>,
  )
}

describe('InstallIdBadge', () => {
  it('shows the short id and carries the whole one in the tooltip', () => {
    renderBadge(ID)
    const badge = screen.getByTestId('install-id')
    expect(badge.textContent).toBe('ID 3f2b9c1e')
    expect(badge.getAttribute('title')).toContain(ID)
  })

  it('copies the whole id', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.assign(navigator, { clipboard: { writeText } })
    renderBadge(ID)
    fireEvent.click(screen.getByTestId('install-id'))
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(ID))
    expect(await screen.findByText('Copied')).toBeTruthy()
  })

  it('renders nothing until the id is known', () => {
    renderBadge('')
    expect(screen.queryByTestId('install-id')).toBeNull()
  })
})
