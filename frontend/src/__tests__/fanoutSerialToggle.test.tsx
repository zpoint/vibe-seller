/**
 * The "run stores one at a time" switch (Schedule.fanout_serial) on the
 * create and edit schedule modals.
 *
 * It is only offered where the server accepts it — an all-stores fanout
 * schedule — and the request carries it only there, because the server
 * refuses the flag (400) on store-bound and single-phase schedules.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { I18nextProvider } from 'react-i18next'
import { i18nTestInstance } from '../test/helpers'
import { makeSchedule } from '../test/factories'

const mockApi = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  del: vi.fn(),
  patch: vi.fn(),
}))
vi.mock('../api', () => ({ api: mockApi }))
vi.mock('../lib/telemetry', () => ({ sendEvent: vi.fn() }))

import { CreateScheduleModal } from '../components/CreateScheduleModal'
import { EditScheduleModal } from '../components/EditScheduleModal'

function wrap(ui: React.ReactElement) {
  return render(<I18nextProvider i18n={i18nTestInstance}>{ui}</I18nextProvider>)
}

const toggle = () => screen.queryByTestId('fanout-serial-toggle')
const checkbox = () =>
  toggle()!.querySelector('input[type="checkbox"]') as HTMLInputElement

beforeEach(() => {
  vi.clearAllMocks()
  mockApi.get.mockResolvedValue({})
  mockApi.post.mockImplementation(async (_url, body) => makeSchedule(body))
  mockApi.put.mockImplementation(async (_url, body) => makeSchedule(body))
})

describe('create', () => {
  function renderCreate(storeId: string | null) {
    wrap(
      <CreateScheduleModal
        storeId={storeId}
        storeName={storeId ? 'Store A' : undefined}
        onClose={vi.fn()}
        onCreated={vi.fn()}
      />,
    )
    fireEvent.change(screen.getByPlaceholderText('Enter task title'), {
      target: { value: 'Monthly download' },
    })
  }
  const submit = () => fireEvent.click(screen.getByRole('button', { name: 'Create' }))

  it('an all-stores fanout offers it, off by default, and sends what is ticked', async () => {
    renderCreate(null)
    expect(checkbox().checked).toBe(false)

    fireEvent.click(checkbox())
    submit()

    await waitFor(() => expect(mockApi.post).toHaveBeenCalled())
    expect(mockApi.post.mock.calls[0][1]).toMatchObject({
      phase_mode: 'fanout',
      fanout_serial: true,
    })
  })

  it('switching to single mode hides it and does not send it', async () => {
    renderCreate(null)
    fireEvent.click(checkbox())
    fireEvent.click(screen.getByLabelText(/Single \(one task\)/))
    expect(toggle()).toBeNull()

    submit()
    await waitFor(() => expect(mockApi.post).toHaveBeenCalled())
    expect(mockApi.post.mock.calls[0][1]).not.toHaveProperty('fanout_serial')
  })

  it('a store-bound schedule never offers it', async () => {
    renderCreate('store-1')
    expect(toggle()).toBeNull()

    submit()
    await waitFor(() => expect(mockApi.post).toHaveBeenCalled())
    expect(mockApi.post.mock.calls[0][1]).not.toHaveProperty('fanout_serial')
  })
})

describe('edit', () => {
  function renderEdit(overrides: Parameters<typeof makeSchedule>[0]) {
    const schedule = makeSchedule({ title: 'Monthly download', ...overrides })
    wrap(
      <EditScheduleModal schedule={schedule} onClose={vi.fn()} onUpdated={vi.fn()} />,
    )
    return schedule
  }
  const save = () => fireEvent.click(screen.getByRole('button', { name: 'Save' }))

  it('shows the stored value and turning it off sends false', async () => {
    const s = renderEdit({ store_id: null, phase_mode: 'fanout', fanout_serial: true })
    expect(checkbox().checked).toBe(true)

    fireEvent.click(checkbox())
    save()

    await waitFor(() => expect(mockApi.put).toHaveBeenCalled())
    expect(mockApi.put.mock.calls[0][0]).toBe(`/api/schedules/${s.id}`)
    expect(mockApi.put.mock.calls[0][1]).toMatchObject({ fanout_serial: false })
  })

  it('turning it on sends true', async () => {
    renderEdit({ store_id: null, phase_mode: 'fanout', fanout_serial: false })
    fireEvent.click(checkbox())
    save()

    await waitFor(() => expect(mockApi.put).toHaveBeenCalled())
    expect(mockApi.put.mock.calls[0][1]).toMatchObject({ fanout_serial: true })
  })

  it.each([
    ['single-phase', { store_id: null, phase_mode: 'single' as const }],
    ['store-bound', { store_id: 'store-1', phase_mode: 'single' as const }],
  ])('a %s schedule neither offers nor sends it', async (_label, overrides) => {
    renderEdit(overrides)
    expect(toggle()).toBeNull()

    save()
    await waitFor(() => expect(mockApi.put).toHaveBeenCalled())
    expect(mockApi.put.mock.calls[0][1]).not.toHaveProperty('fanout_serial')
  })
})
