/**
 * Typing in a task's follow-up box re-renders the box — not the app.
 *
 * Observed: on a long task (~1000 stream items, a large report) typing
 * in the follow-up box lagged. The draft was App state, so each
 * keystroke re-rendered the whole app: the sidebar, the task list and
 * the conversation pane (memoized leaves still get re-reconciled, a
 * thousand of them per key). The draft now lives in a store only the
 * composer subscribes to. Driven through the real App so the test does
 * not depend on where the draft is held.
 */
import { describe, it, expect, vi, afterEach } from 'vitest'
import { act, fireEvent, screen } from '@testing-library/react'
import { bootApp, longTranscript } from '../test/appHarness'

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

describe('typing in the composer', () => {
  afterEach(() => { vi.unstubAllGlobals() })

  it('re-renders neither the conversation nor the sidebar', async () => {
    window.history.pushState({}, '', '/tasks/task-1')
    await bootApp({ messages: longTranscript(300), guideSeen: true })
    const input = await screen.findByTestId('chat-input') as HTMLTextAreaElement
    // The long transcript is on screen.
    expect(screen.getAllByText('widget-1').length).toBeGreaterThan(0)

    const before = { ...renders }
    for (const text of ['h', 'he', 'hel', 'hell', 'hello']) {
      await act(async () => { fireEvent.change(input, { target: { value: text } }) })
    }

    expect(input.value).toBe('hello')
    // The send button reflects the draft — the composer did re-render.
    expect(screen.getByRole('button', { name: /send/i })).toBeEnabled()
    expect(renders.stream - before.stream).toBe(0)
    expect(renders.sidebar - before.sidebar).toBe(0)
  })
})
