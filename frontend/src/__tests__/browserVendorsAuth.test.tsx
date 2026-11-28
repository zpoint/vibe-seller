import { render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { api } from '../api'
import { useBrowserVendors } from '../hooks/useBrowserVendors'
import { vendorBackendOptions } from '../lib/vendorOptions'
import type { BrowserVendor } from '../types'

vi.mock('../api', () => ({ api: { get: vi.fn() } }))

function BackendPicker({ authKey }: { authKey: string | null }) {
  const vendors = useBrowserVendors(authKey)
  const options = vendorBackendOptions(vendors, 'mac')
  return (
    <select
      aria-label="Browser backend"
      data-state={vendors === null ? 'loading' : 'loaded'}
    >
      {options.map(v => <option key={v.key} value={v.key}>{v.key}</option>)}
    </select>
  )
}

describe('browser vendor registry authentication', () => {
  it('refetches and repopulates the backend picker after an anonymous 401 and login', async () => {
    const registered = [
      {
        key: 'chrome', selectable: true, kind: 'plain',
        platforms: ['windows', 'wsl', 'mac', 'linux'],
      },
      {
        key: 'ziniao', selectable: true, kind: 'antidetect',
        platforms: ['windows', 'mac'],
      },
    ] as BrowserVendor[]
    vi.mocked(api.get)
      .mockRejectedValueOnce(new Error('unauthorized'))
      .mockResolvedValueOnce(registered)

    const { rerender } = render(<BackendPicker authKey={null} />)
    const picker = screen.getByRole('combobox', { name: 'Browser backend' })
    await waitFor(() => expect(picker).toHaveAttribute('data-state', 'loaded'))
    expect(picker.children).toHaveLength(0)

    rerender(<BackendPicker authKey="user-1" />)

    await waitFor(() => expect(picker.children).toHaveLength(2))
    expect(screen.getByRole('option', { name: 'chrome' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'ziniao' })).toBeInTheDocument()
    expect(api.get).toHaveBeenCalledTimes(2)
    expect(api.get).toHaveBeenLastCalledWith('/api/browser-vendors')
  })
})
