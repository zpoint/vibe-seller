/**
 * A follow-up is stored server-side before the response comes back, and
 * a profile switch can take a while. Under the 30 s default a delivered
 * message read as failed, and resending it duplicated it — so the send
 * gets the long deadline.
 */
import { describe, it, expect, vi } from 'vitest'
import { sendChatMessage } from '../handlers/sendChatMessage'
import { LONG_TIMEOUT_MS } from '../lib/requestDeadline'
import { makeTask } from '../test/factories'

vi.mock('../lib/telemetry', () => ({ sendEvent: vi.fn(), lengthBucket: () => 'small' }))

describe('follow-up send deadline', () => {
  it('uses the long deadline', async () => {
    const post = vi.fn(async () => ({ content: 'hi' }))
    const noop = vi.fn()
    await sendChatMessage({
      api: { post }, selectedTask: makeTask({ id: 't1' }), chatInput: 'hi',
      attachments: [], profileId: 'p', conversationItems: [],
      sendingRef: { current: false }, setChatInput: noop, setAttachments: noop,
      setAgentMessages: noop, setConversationItems: noop, setSelectedTask: noop,
      setTasks: noop,
    })
    expect(post).toHaveBeenCalledWith(
      '/api/tasks/t1/messages', expect.anything(), { timeoutMs: LONG_TIMEOUT_MS },
    )
  })
})
