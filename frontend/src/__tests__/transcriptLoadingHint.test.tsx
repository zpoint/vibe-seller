/**
 * While an opened task's transcript loads, the pane says so.
 *
 * Observed (review): the header switched to the new task at once, but
 * until its (unpaginated) transcript arrived the conversation area was
 * simply empty — indistinguishable from a task with no messages.
 */
import { describe, it, expect, vi, afterEach } from 'vitest'
import { act, screen } from '@testing-library/react'
import { bootApp } from '../test/appHarness'

vi.mock('../lib/telemetry', () => ({
  sendEvent: vi.fn(), lengthBucket: () => 'small', ageBucket: () => 'new',
  registerUserHash: vi.fn(), unregisterUserHash: vi.fn(),
}))

describe('transcript loading hint', () => {
  afterEach(() => { vi.unstubAllGlobals() })

  it('shows while the transcript is on the way and goes when it lands', async () => {
    window.history.pushState({}, '', '/tasks/task-1')
    let release: () => void = () => {}
    await bootApp({
      route: (url, init) => url === '/api/tasks/task-1/messages' && (init?.method ?? 'GET') === 'GET'
        ? new Promise(resolve => {
          release = () => resolve({ ok: true, status: 200, json: async () => [{ role: 'assistant', content: 'Checked widget-001' }] })
        })
        : undefined,
    })
    expect(await screen.findByText('Example task', { selector: 'h2' })).toBeInTheDocument()
    expect(screen.getByTestId('transcript-loading')).toBeInTheDocument()

    await act(async () => { release() })
    expect(await screen.findByText('Checked widget-001')).toBeInTheDocument()
    expect(screen.queryByTestId('transcript-loading')).toBeNull()
  })
})
