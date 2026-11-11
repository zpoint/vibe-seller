/**
 * Updating the signed-in user does not cancel the open task's load.
 *
 * Observed (review): the route effect that opens tasks re-ran on every
 * `currentUser` change. The Auto/Plan switch saves the user's default
 * mode (a new user object), so flipping it while the task's transcript
 * was still loading retired that load — the conversation stayed empty.
 */
import { describe, it, expect, vi, afterEach } from 'vitest'
import { act, fireEvent, screen } from '@testing-library/react'
import { bootApp } from '../test/appHarness'

vi.mock('../lib/telemetry', () => ({
  sendEvent: vi.fn(), lengthBucket: () => 'small', ageBucket: () => 'new',
  registerUserHash: vi.fn(), unregisterUserHash: vi.fn(),
}))

const ok = (body: unknown) => ({ ok: true, status: 200, json: async () => body })

describe('the open task while the user object changes', () => {
  afterEach(() => { vi.unstubAllGlobals() })

  it('a mode switch during the transcript load keeps the load', async () => {
    window.history.pushState({}, '', '/stores/store-1/tasks/task-1')
    let releaseTranscript: () => void = () => {}
    await bootApp({
      task: { status: 'pending', store_id: 'store-1', plan_mode: false },
      route: (url, init) => {
        if (url === '/api/stores') return [{ id: 'store-1', name: 'Example store', browser_backend: 'chrome' }]
        if (url === '/api/tasks/task-1/messages' && (init?.method ?? 'GET') === 'GET') {
          return new Promise(resolve => {
            releaseTranscript = () => resolve(ok([{ role: 'assistant', content: 'Checked widget-001' }]))
          })
        }
        if (url === '/api/tasks/task-1/review-plan') return {}
        return undefined
      },
    })

    // The task header is up; its transcript is still loading.
    const review = await screen.findByRole('button', { name: /^review$/i, pressed: false })
    await act(async () => { fireEvent.click(review) })
    expect(review).toHaveAttribute('aria-pressed', 'true')

    await act(async () => { releaseTranscript() })
    expect(await screen.findByText('Checked widget-001')).toBeInTheDocument()
  })
})
