/**
 * The assistant composer must return to Send when the TURN ends — it
 * must not wait for the process to exit.
 *
 * The workspace assistant keeps ONE live CLI process across turns
 * (stdin stays open), so `ws_assistant_done` — the process-exit event —
 * only arrives on stop or crash. With that as the only clearer of
 * `wsAssistantRunning`, every real reply left the composer showing
 * Stop, the thinking indicator could linger, and the visible control
 * invited the user to kill the session they were still talking to.
 * The backend now ends each turn with `ws_assistant_turn_done`; this
 * pins the frontend clearing the running state on it.
 *
 * On origin/main the turn-done event is unknown to useSSE, so this
 * test stays on Stop and fails — the negative control. (The e2e mock
 * CLI used to exit after every result, which made the old lifecycle
 * look fine; the mock now models the real one.)
 */
import { describe, it, expect, beforeEach } from 'vitest'
import { fireEvent, screen } from '@testing-library/react'

const WS_STRUCTURED = {
  skills: [],
  store_profiles: [],
  project_knowledge: [],
  local_knowledge: [],
  root_files: [],
}

describe('workspace assistant running state', () => {
  beforeEach(() => {
    window.history.pushState({}, '', '/workspace')
  })

  it('returns the composer to Send on ws_assistant_turn_done', async () => {
    const { bootApp } = await import('../test/appHarness')
    const view = await bootApp({
      route: url =>
        url.split('?')[0] === '/api/workspace/structured'
          ? WS_STRUCTURED
          : undefined,
    })

    // Open the AI Chat mode of the Workspace view.
    fireEvent.click(screen.getByRole('button', { name: /AI Chat/ }))

    // Send one message: running starts, Stop shows, thinking shows.
    const textarea = screen.getByPlaceholderText(
      'Tell the AI something about your stores...',
    )
    fireEvent.change(textarea, { target: { value: 'tidy the notes' } })
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))
    expect(view.fetchMock).toHaveBeenCalledWith(
      '/api/workspace/assistant/message',
      expect.objectContaining({ method: 'POST' }),
    )
    expect(await screen.findByRole('button', { name: 'Stop' })).toBeEnabled()
    expect(screen.getByText('AI is thinking...')).toBeInTheDocument()

    // The reply arrives — the assistant is done talking but the
    // PROCESS is still alive: only the per-turn event ends the turn.
    view.emit({
      type: 'ws_assistant_message',
      session_key: '__ws_assistant_user-1__',
      role: 'assistant',
      content: 'Tidied the notes.',
    })
    expect(await screen.findByText('Tidied the notes.')).toBeInTheDocument()
    view.emit({
      type: 'ws_assistant_turn_done',
      session_key: '__ws_assistant_user-1__',
    })

    // Turn over: Send again, no Stop, no lingering thinking bubble.
    // Pre-fix this waited for ws_assistant_done — which the live
    // session never sent.
    expect(
      await screen.findByRole('button', { name: 'Send' }),
    ).toBeEnabled()
    expect(screen.queryByRole('button', { name: 'Stop' })).toBeNull()
    expect(screen.queryByText('AI is thinking...')).toBeNull()
  })
})
