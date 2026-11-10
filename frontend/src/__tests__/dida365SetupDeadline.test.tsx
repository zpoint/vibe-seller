/**
 * Installing the TickTick MCP server (git clone + uv install) gets the
 * long request deadline. Under the 30 s default a first install on a
 * slow link was aborted client-side and reported as failed while the
 * server was still installing.
 */
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { I18nextProvider } from 'react-i18next'
import { i18nTestInstance } from '../test/helpers'
import { LONG_TIMEOUT_MS } from '../lib/requestDeadline'

const mockApi = vi.hoisted(() => ({
  get: vi.fn(), post: vi.fn(), put: vi.fn(), del: vi.fn(), patch: vi.fn(),
}))
vi.mock('../api', () => ({ api: mockApi }))

import { Dida365Panel } from '../components/settings/Dida365Panel'

describe('Dida365 MCP setup', () => {
  it('asks for the long deadline', async () => {
    mockApi.get.mockResolvedValue({ connected: false, service_type: '', project_id: '' })
    mockApi.post.mockRejectedValue(new Error('stop here'))
    render(<I18nextProvider i18n={i18nTestInstance}><Dida365Panel /></I18nextProvider>)

    fireEvent.click(await screen.findByTestId('dida365-toggle'))
    await waitFor(() => expect(screen.getByTestId('dida365-panel')).toBeInTheDocument())
    fireEvent.change(screen.getByTestId('dida365-client-id'), { target: { value: 'client-id' } })
    fireEvent.change(screen.getByTestId('dida365-client-secret'), { target: { value: 'client-secret' } })
    fireEvent.click(screen.getByRole('button', { name: /authorize/i }))

    await waitFor(() => expect(mockApi.post).toHaveBeenCalled())
    expect(mockApi.post).toHaveBeenCalledWith(
      '/api/dida365/setup-mcp', undefined, { timeoutMs: LONG_TIMEOUT_MS })
  })
})
