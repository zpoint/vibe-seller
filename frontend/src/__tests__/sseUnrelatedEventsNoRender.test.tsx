/**
 * An SSE event about a task that is not on screen re-renders nothing.
 *
 * Observed: every `task_message` (one per streamed token, for every
 * running task on the server), `task_usage` and `task_update` event
 * rebuilt the task list with `prev.map(...)` — a new array even when no
 * row matched or changed. Each event therefore re-rendered the whole app
 * (sidebar, task list, the open task's conversation), so a background
 * task streaming its output kept the main thread busy while the user
 * typed or clicked in a long task. A list patch now keeps the list's
 * identity when nothing in it changed.
 */
import { describe, it, expect, vi, afterEach } from 'vitest'
import { screen } from '@testing-library/react'
import { bootApp, longTranscript } from '../test/appHarness'
import { makeTask } from '../test/factories'

const renders = vi.hoisted(() => ({ stream: 0, sidebar: 0 }))

vi.mock('../components/conversation/ConversationStream', async (orig) => {
  const mod = await orig<typeof import('../components/conversation/ConversationStream')>()
  return {
    ...mod,
    ConversationStream: (p: Parameters<typeof mod.ConversationStream>[0]) => {
      renders.stream += 1
      return mod.ConversationStream(p)
    },
  }
})
vi.mock('../components/Sidebar', async (orig) => {
  const mod = await orig<typeof import('../components/Sidebar')>()
  return {
    ...mod,
    Sidebar: (p: Parameters<typeof mod.Sidebar>[0]) => {
      renders.sidebar += 1
      return mod.Sidebar(p)
    },
  }
})
vi.mock('../lib/telemetry', () => ({
  sendEvent: vi.fn(), lengthBucket: () => 'small', ageBucket: () => 'new',
  registerUserHash: vi.fn(), unregisterUserHash: vi.fn(),
}))

describe('SSE events for other tasks', () => {
  afterEach(() => { vi.unstubAllGlobals() })

  it('re-render neither the open conversation nor the sidebar', async () => {
    window.history.pushState({}, '', '/tasks/task-1')
    // The list also holds a task that is already running elsewhere.
    const open = makeTask({ id: 'task-1', store_id: null, status: 'completed', title: 'Example task' })
    const running = makeTask({ id: 'task-2', store_id: null, status: 'running', title: 'Background task' })
    const { emit } = await bootApp({ task: open, tasks: [open, running], messages: longTranscript(200), guideSeen: true })
    await screen.findByTestId('chat-input')

    const before = { ...renders }
    for (let i = 0; i < 10; i++) {
      // Tokens streamed by the running task in the list…
      emit({ type: 'task_message', task_id: 'task-2', role: 'delta', content: `tok${i} ` })
      // …and by a task in another store's list.
      emit({ type: 'task_message', task_id: 'task-elsewhere', role: 'delta', content: 'x' })
    }
    emit({ type: 'task_usage', task_id: 'task-elsewhere', input_tokens: 10 })
    emit({ type: 'task_update', task_id: 'task-elsewhere', status: 'running', error: null })

    expect(renders.stream - before.stream).toBe(0)
    expect(renders.sidebar - before.sidebar).toBe(0)
  })

  it('an event that does change a listed task still shows', async () => {
    window.history.pushState({}, '', '/tasks/task-1')
    const open = makeTask({ id: 'task-1', store_id: null, status: 'completed', title: 'Example task' })
    const queued = makeTask({ id: 'task-2', store_id: null, status: 'queued', title: 'Background task' })
    const { emit } = await bootApp({ task: open, tasks: [open, queued], guideSeen: true })
    await screen.findByTestId('chat-input')

    emit({ type: 'task_update', task_id: 'task-2', status: 'failed', error: 'boom' })
    const row = screen.getByText('Background task').closest('button')!
    expect(row.textContent).toMatch(/failed/i)
  })
})
