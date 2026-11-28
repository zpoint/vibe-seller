/**
 * Create-store feedback: a duplicate is rejected inline, a server failure
 * stays in the form instead of alert(), and one in-flight request disables
 * Create until it settles. The success path still closes and reloads.
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, screen, waitFor } from '@testing-library/react'
import { bootApp } from '../test/appHarness'
import { makeStore } from '../test/factories'

vi.mock('../lib/telemetry', () => ({
  sendEvent: vi.fn(), lengthBucket: () => 'small', ageBucket: () => 'new',
  registerUserHash: vi.fn(), unregisterUserHash: vi.fn(),
}))

const ok = (body: unknown) => ({
  ok: true,
  status: 200,
  json: async () => body,
}) as unknown as Response

const storePosts = (fetchMock: ReturnType<typeof vi.fn>) =>
  fetchMock.mock.calls.filter(
    ([url, init]) => url === '/api/stores' && init?.method === 'POST',
  )

function openForm() {
  fireEvent.click(screen.getByRole('button', { name: '+ New Store' }))
  return screen.getByPlaceholderText('Store Name...')
}

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('create-store feedback', () => {
  it.each([
    { attempted: ' acme test ', label: 'an exact duplicate ignoring case' },
    { attempted: 'acme_test!', label: 'a different name with the same slug' },
  ])('rejects $label inline without alert or a POST', async ({ attempted }) => {
    const alertSpy = vi.spyOn(window, 'alert').mockImplementation(() => {})
    const existing = makeStore({ id: 'store-a', name: 'Acme Test' })
    const { fetchMock } = await bootApp({
      route: (url, init) => {
        if (url === '/api/stores' && init?.method === 'POST') {
          return ok(makeStore({ name: attempted }))
        }
        if (url === '/api/stores') return [existing]
        return undefined
      },
    })

    const input = openForm()
    fireEvent.change(input, { target: { value: attempted } })

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'This name conflicts with “Acme Test”',
    )
    expect(screen.getByRole('button', { name: /^Create$/ })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: /^Create$/ }))
    expect(alertSpy).not.toHaveBeenCalled()
    expect(storePosts(fetchMock)).toHaveLength(0)
  })

  it('shows a server collision inline and never calls alert', async () => {
    const alertSpy = vi.spyOn(window, 'alert').mockImplementation(() => {})
    const detail = "Name collides with existing store 'Fresh Store': both reduce to the same folder name 'fresh'. Choose a different name."
    const { fetchMock } = await bootApp({
      route: (url, init) => {
        if (url === '/api/stores' && init?.method === 'POST') {
          return Promise.reject(new Error(detail))
        }
        if (url === '/api/stores') return [makeStore({ name: 'Other Shop' })]
        return undefined
      },
    })

    fireEvent.change(openForm(), { target: { value: 'Fresh!' } })
    fireEvent.click(screen.getByRole('button', { name: /^Create$/ }))

    expect(await screen.findByRole('alert')).toHaveTextContent(
      `Couldn't create store: ${detail}`,
    )
    expect(alertSpy).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: /^Create$/ })).toBeEnabled()
    expect(storePosts(fetchMock)).toHaveLength(1)
  })

  it('disables Create with a spinner in flight, then completes normally', async () => {
    const alertSpy = vi.spyOn(window, 'alert').mockImplementation(() => {})
    const existing = makeStore({ id: 'store-a', name: 'Existing Shop' })
    const created = makeStore({ id: 'store-b', name: 'Fresh Shop' })
    let release: () => void = () => {}
    const pending = new Promise<Response>(resolve => {
      release = () => resolve(ok(created))
    })
    let createdOnServer = false
    const { fetchMock } = await bootApp({
      route: (url, init) => {
        if (url === '/api/stores' && init?.method === 'POST') {
          createdOnServer = true
          return pending
        }
        if (url === '/api/stores') return createdOnServer ? [existing, created] : [existing]
        return undefined
      },
    })

    const input = openForm()
    fireEvent.change(input, { target: { value: 'Fresh Shop' } })
    fireEvent.click(screen.getByRole('button', { name: /^Create$/ }))

    const busyButton = await screen.findByRole('button', { name: /^Creating…$/ })
    expect(busyButton).toBeDisabled()
    expect(screen.getByTestId('store-create-spinner')).toBeInTheDocument()
    fireEvent.click(busyButton)
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(storePosts(fetchMock)).toHaveLength(1)

    await act(async () => { release(); await pending })
    await waitFor(() => {
      expect(screen.queryByPlaceholderText('Store Name...')).not.toBeInTheDocument()
    })
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(alertSpy).not.toHaveBeenCalled()
    expect(storePosts(fetchMock)).toHaveLength(1)
    const body = JSON.parse(String(storePosts(fetchMock)[0][1]?.body)) as Record<string, unknown>
    expect(body).toMatchObject({ name: 'Fresh Shop', browser_backend: 'chrome' })
    expect(await screen.findByRole('button', { name: /Fresh Shop chrome/ })).toBeInTheDocument()
  })
})
