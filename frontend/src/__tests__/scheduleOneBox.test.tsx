/**
 * The schedule dialogs are one prompt box, like the task dialog: no
 * Title field; the server derives the title from the prompt
 * (app/task_title.py). On edit, only a changed prompt is sent — with a
 * blank title so it is re-derived — and an untouched prompt keeps both.
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

const wrap = (ui: React.ReactNode) =>
  render(<I18nextProvider i18n={i18nTestInstance}>{ui}</I18nextProvider>)

const prompt = () => screen.getByLabelText('What should run each time') as HTMLTextAreaElement

describe('create', () => {
  beforeEach(() => {
    wrap(<CreateScheduleModal storeId="store-a" onClose={() => {}} onCreated={() => {}} />)
  })

  it('has one prompt box and no title field', () => {
    expect(prompt()).toBeTruthy()
    expect(screen.queryByText('Title')).toBeNull()
    expect(screen.queryByPlaceholderText('Enter task title')).toBeNull()
  })

  it('cannot be created with an empty prompt', () => {
    expect((screen.getByRole('button', { name: 'Create' }) as HTMLButtonElement).disabled).toBe(true)
  })

  it('sends the prompt as the description and no title', async () => {
    fireEvent.change(prompt(), { target: { value: '  Check the ads every morning.  ' } })
    fireEvent.click(screen.getByRole('button', { name: 'Create' }))
    await waitFor(() => expect(mockPost).toHaveBeenCalled())
    const body = mockPost.mock.calls[0][1]
    expect(body.description).toBe('Check the ads every morning.')
    expect(body).not.toHaveProperty('title')
  })
})

describe('edit', () => {
  const save = () => fireEvent.click(screen.getByRole('button', { name: /save/i }))

  it('shows the prompt, and an untouched prompt sends neither title nor description', async () => {
    wrap(<EditScheduleModal
      schedule={makeSchedule({ title: 'Ads check', description: 'Check the ads.' })}
      onClose={() => {}} onUpdated={() => {}} />)
    expect(prompt().value).toBe('Check the ads.')
    save()
    await waitFor(() => expect(mockPut).toHaveBeenCalled())
    const body = mockPut.mock.calls[0][1]
    expect(body).not.toHaveProperty('title')
    expect(body).not.toHaveProperty('description')
  })

  it('a changed prompt is sent with a blank title to re-derive it', async () => {
    wrap(<EditScheduleModal
      schedule={makeSchedule({ title: 'Ads check', description: 'Check the ads.' })}
      onClose={() => {}} onUpdated={() => {}} />)
    fireEvent.change(prompt(), { target: { value: 'Check the ads and the coupons.' } })
    save()
    await waitFor(() => expect(mockPut).toHaveBeenCalled())
    expect(mockPut.mock.calls[0][1]).toMatchObject({
      title: '',
      description: 'Check the ads and the coupons.',
    })
  })

  it('a title-only schedule shows its title as the prompt', async () => {
    wrap(<EditScheduleModal
      schedule={makeSchedule({ title: 'Download the report', description: null })}
      onClose={() => {}} onUpdated={() => {}} />)
    expect(prompt().value).toBe('Download the report')
    save()
    await waitFor(() => expect(mockPut).toHaveBeenCalled())
    expect(mockPut.mock.calls[0][1]).not.toHaveProperty('description')
  })
})
