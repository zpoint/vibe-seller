/**
 * On a phone, Back leaves a task that is opening or failed to open.
 *
 * Observed (review): the mobile Back button cleared `selectedTask` (or
 * the schedule), but the opening / failed-to-open pane is driven by the
 * route's task load, so Back did nothing there and the user was stuck
 * on the error pane. Back now leaves the task's route for its list.
 */
import { describe, it, expect, vi, afterEach } from 'vitest'
import { act, fireEvent, screen } from '@testing-library/react'
import { bootApp } from '../test/appHarness'

vi.mock('../hooks/useIsMobile', () => ({ useIsMobile: () => true }))
vi.mock('../lib/telemetry', () => ({
  sendEvent: vi.fn(), lengthBucket: () => 'small', ageBucket: () => 'new',
  registerUserHash: vi.fn(), unregisterUserHash: vi.fn(),
}))

describe('mobile Back on a task that failed to open', () => {
  afterEach(() => { vi.unstubAllGlobals() })

  it('returns to the task list', async () => {
    window.history.pushState({}, '', '/tasks/task-1')
    const { router } = await bootApp({
      route: url => url === '/api/tasks/task-1' ? Promise.reject(new TypeError('Failed to fetch')) : undefined,
    })
    expect(screen.getByTestId('task-open-error')).toBeInTheDocument()

    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /back/i })) })

    expect(router.state.location.pathname).toBe('/tasks')
    expect(screen.queryByTestId('task-open-error')).toBeNull()
  })
})
