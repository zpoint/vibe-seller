/**
 * Sending a follow-up never fails silently and never hangs.
 *
 * Observed: the send handler cleared the box, then swallowed any error
 * into console.error — against a server that was down the message
 * vanished with no sign it was not delivered; against one that never
 * answered, the request stayed pending forever with nothing on screen,
 * and its de-dupe guard stayed set, dropping every later send. Now the
 * button shows the send is in flight, and a failure (refused, error, or
 * past the request deadline) is said in words with the text put back.
 */
import { describe, it, expect, vi, afterEach } from 'vitest'
import { act, fireEvent, screen } from '@testing-library/react'
import { bootApp } from '../test/appHarness'
import { LONG_TIMEOUT_MS } from '../lib/requestDeadline'

vi.mock('../lib/telemetry', () => ({
  sendEvent: vi.fn(), lengthBucket: () => 'small', ageBucket: () => 'new',
  registerUserHash: vi.fn(), unregisterUserHash: vi.fn(),
}))

const isSend = (url: string, init?: RequestInit) =>
  url === '/api/tasks/task-1/messages' && init?.method === 'POST'

async function typeAndSend(text: string) {
  const input = screen.getByTestId('chat-input') as HTMLTextAreaElement
  await act(async () => { fireEvent.change(input, { target: { value: text } }) })
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: /send/i })) })
  return input
}

describe('follow-up send feedback', () => {
  afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals() })

  it('a server that never answers: shows sending, then a timeout error with the text back', async () => {
    window.history.pushState({}, '', '/tasks/task-1')
    await bootApp({
      route: (url, init) => isSend(url, init)
        ? new Promise((_resolve, reject) => {
          init?.signal?.addEventListener('abort', () =>
            reject(new DOMException('aborted', 'AbortError')))
        })
        : undefined,
    })
    await screen.findByTestId('chat-input')
    vi.useFakeTimers()
    const input = await typeAndSend('please re-check the example listing')

    // In flight: visibly waiting, and the box was cleared.
    expect(screen.getByTestId('chat-sending')).toBeDisabled()
    expect(input.value).toBe('')

    // A follow-up waits the long deadline (it may switch profile).
    await act(async () => { await vi.advanceTimersByTimeAsync(LONG_TIMEOUT_MS) })

    expect(screen.getByTestId('chat-send-error')).toHaveTextContent(/did not answer in time/i)
    expect(input.value).toBe('please re-check the example listing')
    expect(screen.queryByTestId('chat-sending')).toBeNull()
  })

  it('a server that is down: fails at once with the text back', async () => {
    window.history.pushState({}, '', '/tasks/task-1')
    await bootApp({
      route: (url, init) => isSend(url, init)
        ? Promise.reject(new TypeError('Failed to fetch'))
        : undefined,
    })
    await screen.findByTestId('chat-input')
    const input = await typeAndSend('hello')

    expect(screen.getByTestId('chat-send-error')).toHaveTextContent(/not sent: Failed to fetch/i)
    expect(input.value).toBe('hello')
    // And the next send is not swallowed by a stuck de-dupe guard.
    expect(screen.getByRole('button', { name: /send/i })).toBeEnabled()
  })
})
