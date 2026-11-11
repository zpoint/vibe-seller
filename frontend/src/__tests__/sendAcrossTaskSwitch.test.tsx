/**
 * A follow-up belongs to the task it was sent on, even when its response
 * arrives after the user opened another task.
 *
 * Observed (review): the send handler applied its response to whatever
 * task was open — appending task A's message to task B's conversation and
 * possibly marking B queued; a send that failed after the switch lost its
 * text (A's composer was gone); and one shared in-flight guard silently
 * dropped a send on B while A's was pending.
 */
import { describe, it, expect, vi, afterEach } from 'vitest'
import { act, fireEvent, screen } from '@testing-library/react'
import { bootApp } from '../test/appHarness'
import { makeTask } from '../test/factories'

vi.mock('../lib/telemetry', () => ({
  sendEvent: vi.fn(), lengthBucket: () => 'small', ageBucket: () => 'new',
  registerUserHash: vi.fn(), unregisterUserHash: vi.fn(),
}))

const ok = (body: unknown) => ({ ok: true, status: 200, json: async () => body })
const second = makeTask({ id: 'task-2', store_id: null, status: 'completed', title: 'Second task' })

function serveSecond(url: string) {
  if (url === '/api/tasks/task-2') return second
  if (url === '/api/tasks/task-2/messages' || url === '/api/tasks/task-2/steps') return []
  if (url.startsWith('/api/tasks/task-2/') && url.endsWith('/pending')) return { pending: false }
  return undefined
}

async function typeAndSend(text: string) {
  const input = await screen.findByTestId('chat-input')
  await act(async () => { fireEvent.change(input, { target: { value: text } }) })
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: /^send$/i })) })
}

async function open(router: Awaited<ReturnType<typeof bootApp>>['router'], taskId: string, title: string) {
  await act(async () => { await router.navigate({ to: '/tasks/$taskId', params: { taskId } }) })
  await screen.findByText(title, { selector: 'h2' })
}

describe('a follow-up across a task switch', () => {
  afterEach(() => { vi.unstubAllGlobals() })

  it("A's late response does not land in B", async () => {
    window.history.pushState({}, '', '/tasks/task-1')
    let settleA: (v: unknown) => void = () => {}
    const { router } = await bootApp({
      route: (url, init) => {
        if (url === '/api/tasks/task-1/messages' && init?.method === 'POST') {
          return new Promise(resolve => { settleA = resolve })
        }
        return serveSecond(url)
      },
    })
    await typeAndSend('message for the first task')
    await open(router, 'task-2', 'Second task')

    await act(async () => { settleA(ok({ content: 'message for the first task', woken: true, profile_switched: true })) })

    expect(screen.queryByText('message for the first task')).toBeNull()
    // B's header keeps B's status (A's row in the list may say queued).
    const header = screen.getByText('Second task', { selector: 'h2' }).closest('.flex-wrap')!
    expect(header.textContent).toMatch(/completed/i)
    expect(header.textContent).not.toMatch(/queued/i)
  })

  it("A's failed send is restored on A, not into B's box", async () => {
    window.history.pushState({}, '', '/tasks/task-1')
    let failA: (e: unknown) => void = () => {}
    const { router } = await bootApp({
      route: (url, init) => {
        if (url === '/api/tasks/task-1/messages' && init?.method === 'POST') {
          return new Promise((_resolve, reject) => { failA = reject })
        }
        return serveSecond(url)
      },
    })
    await typeAndSend('draft for the first task')
    await open(router, 'task-2', 'Second task')
    await act(async () => { failA(new TypeError('Failed to fetch')) })
    expect((screen.getByTestId('chat-input') as HTMLTextAreaElement).value).toBe('')

    await open(router, 'task-1', 'Example task')
    expect((screen.getByTestId('chat-input') as HTMLTextAreaElement).value).toBe('draft for the first task')
    expect(screen.getByTestId('chat-send-error')).toHaveTextContent(/not sent/i)
  })

  it("a send on B is not dropped while A's is in flight", async () => {
    window.history.pushState({}, '', '/tasks/task-1')
    const posted: string[] = []
    const { router } = await bootApp({
      route: (url, init) => {
        if (url.endsWith('/messages') && init?.method === 'POST') {
          posted.push(url)
          return url.includes('task-1') ? new Promise(() => {}) : ok({ content: 'for the second task' })
        }
        return serveSecond(url)
      },
    })
    await typeAndSend('for the first task')
    await open(router, 'task-2', 'Second task')
    await typeAndSend('for the second task')

    expect(posted).toEqual(['/api/tasks/task-1/messages', '/api/tasks/task-2/messages'])
  })
})
