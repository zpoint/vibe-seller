/**
 * The schedule form's "Let me review the plan before it runs" switch:
 * off by default (the schedule runs its instructions directly), sent as
 * `plan_mode` on create, and on edit only when it changed.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { I18nextProvider } from 'react-i18next'
import { CreateScheduleModal } from '../components/CreateScheduleModal'
import { EditScheduleModal } from '../components/EditScheduleModal'
import { api } from '../api'
import { i18nTestInstance } from '../test/helpers'
import { makeSchedule } from '../test/factories'

vi.mock('../api', () => ({
  api: { get: vi.fn(), post: vi.fn(), put: vi.fn() },
}))
vi.mock('../lib/telemetry', () => ({ sendEvent: vi.fn() }))

const mockGet = api.get as ReturnType<typeof vi.fn>
const mockPost = api.post as ReturnType<typeof vi.fn>
const mockPut = api.put as ReturnType<typeof vi.fn>

beforeEach(() => {
  mockGet.mockReset().mockResolvedValue({})
  mockPost.mockReset().mockResolvedValue(makeSchedule())
  mockPut.mockReset().mockResolvedValue(makeSchedule())
})

function renderCreate() {
  render(
    <I18nextProvider i18n={i18nTestInstance}>
      <CreateScheduleModal storeId="store-a" onClose={() => {}} onCreated={() => {}} />
    </I18nextProvider>,
  )
  // One prompt box; the server derives the title from it.
  const prompt = document.getElementById('schedule-prompt') as HTMLTextAreaElement
  fireEvent.change(prompt, { target: { value: 'Weekly check' } })
}

describe('create', () => {
  it('the hint describes only the current choice and switches with it', () => {
    renderCreate()
    const off = 'The AI runs it automatically at each scheduled time.'
    const on = 'The AI writes a plan first, and it runs only after you approve the plan.'
    expect(screen.getByText(off)).toBeTruthy()
    expect(screen.queryByText(on)).toBeNull()
    fireEvent.click(screen.getByTestId('schedule-review-plan'))
    expect(screen.getByText(on)).toBeTruthy()
    expect(screen.queryByText(off)).toBeNull()
  })

  it('is off by default and creates an auto schedule', async () => {
    renderCreate()
    const box = screen.getByTestId('schedule-review-plan') as HTMLInputElement
    expect(box.checked).toBe(false)
    expect(screen.getByText('Let me review the plan before it runs')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: /create/i }))
    await waitFor(() => expect(mockPost).toHaveBeenCalled())
    expect(mockPost.mock.calls[0][1]).toMatchObject({ plan_mode: false })
  })

  it('sends plan_mode true when turned on', async () => {
    renderCreate()
    fireEvent.click(screen.getByTestId('schedule-review-plan'))
    // A new schedule has nothing running yet — no "stops running" note.
    expect(screen.queryByTestId('schedule-review-plan-note')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: /create/i }))
    await waitFor(() => expect(mockPost).toHaveBeenCalled())
    expect(mockPost.mock.calls[0][1]).toMatchObject({ plan_mode: true })
  })
})

describe('edit', () => {
  function renderEdit(planMode: boolean) {
    render(
      <I18nextProvider i18n={i18nTestInstance}>
        <EditScheduleModal
          schedule={makeSchedule({ id: 'sch-1', title: 'Weekly check', plan_mode: planMode })}
          onClose={() => {}}
          onUpdated={() => {}}
        />
      </I18nextProvider>,
    )
  }

  it('leaves plan_mode out when unchanged', async () => {
    renderEdit(false)
    fireEvent.click(screen.getByRole('button', { name: /save/i }))
    await waitFor(() => expect(mockPut).toHaveBeenCalled())
    expect(mockPut.mock.calls[0][1]).not.toHaveProperty('plan_mode')
  })

  it('turning review on warns that runs stop until approval, and sends it', async () => {
    renderEdit(false)
    fireEvent.click(screen.getByTestId('schedule-review-plan'))
    expect(screen.getByTestId('schedule-review-plan-note')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: /save/i }))
    await waitFor(() => expect(mockPut).toHaveBeenCalled())
    expect(mockPut.mock.calls[0][1]).toMatchObject({ plan_mode: true })
  })

  it('turning review off sends plan_mode false', async () => {
    renderEdit(true)
    const box = screen.getByTestId('schedule-review-plan') as HTMLInputElement
    expect(box.checked).toBe(true)
    fireEvent.click(box)
    fireEvent.click(screen.getByRole('button', { name: /save/i }))
    await waitFor(() => expect(mockPut).toHaveBeenCalled())
    expect(mockPut.mock.calls[0][1]).toMatchObject({ plan_mode: false })
  })
})
