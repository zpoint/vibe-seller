/**
 * One answer renders once. Claude Code's ``result`` event repeats the
 * turn's last assistant text, and both are persisted and streamed
 * (role ``assistant``, then role ``result``) — which drew every final
 * answer twice: once as a message, once as the result card.
 *
 * Pins, for the history rebuild (lib/conversation) and the live SSE
 * path (hooks/useSSE) alike: the result card replaces the plain message
 * right before it when the text is the same; different text keeps
 * both; and every turn's result is a card live, as on reload.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { useState } from 'react'
import { useSSE } from '../hooks/useSSE'
import { buildConversationItems } from '../lib/conversation'
import type { ConversationItem, Task } from '../types'
import { makeTask } from '../test/factories'

const ANSWER = 'Sales today: 12 orders.\n\n| store | orders |\n|---|---|\n| acme | 12 |'

const shape = (items: ConversationItem[]) =>
  items.map(i => (i.type === 'result' ? `result:${i.result}` : i.type === 'agent_message' ? `msg:${i.message?.content}` : i.type))

describe('history rebuild', () => {
  const task = { id: 't1', status: 'completed', plan_mode: false } as Task

  it('a result repeating the message before it renders once, as the card', () => {
    const items = buildConversationItems(
      [
        { role: 'user', content: 'how much did we sell today?' },
        { role: 'assistant', content: ANSWER },
        { role: 'result', content: ANSWER },
      ],
      task,
    )
    expect(shape(items)).toEqual(['user_message', `result:${ANSWER}`])
  })

  it('differences in surrounding whitespace still count as the same text', () => {
    const items = buildConversationItems(
      [{ role: 'assistant', content: `${ANSWER}\n` }, { role: 'result', content: ANSWER }],
      task,
    )
    expect(shape(items)).toEqual([`result:${ANSWER}`])
  })

  it('a different message before the result is kept', () => {
    const items = buildConversationItems(
      [
        { role: 'assistant', content: 'Checking the orders page.' },
        { role: 'result', content: ANSWER },
      ],
      task,
    )
    expect(shape(items)).toEqual(['msg:Checking the orders page.', `result:${ANSWER}`])
  })

  it('only the message right before is folded, never an earlier one', () => {
    const items = buildConversationItems(
      [
        { role: 'assistant', content: ANSWER },
        { role: 'thinking', content: 'double-check' },
        { role: 'result', content: ANSWER },
      ],
      task,
    )
    expect(shape(items)).toEqual([`msg:${ANSWER}`, 'thinking', `result:${ANSWER}`])
  })

  it('two turns: each answer once, under its own question', () => {
    const items = buildConversationItems(
      [
        { role: 'user', content: 'q1' },
        { role: 'assistant', content: 'a1' },
        { role: 'result', content: 'a1' },
        { role: 'user', content: 'q2' },
        { role: 'assistant', content: 'a2' },
        { role: 'result', content: 'a2' },
      ],
      { ...task, result: 'a2' },
    )
    expect(shape(items)).toEqual(['user_message', 'result:a1', 'user_message', 'result:a2'])
  })
})

describe('live (SSE)', () => {
  type Listener = ((evt: MessageEvent) => void) | null
  let es: { onmessage: Listener; close: ReturnType<typeof vi.fn> }[] = []

  beforeEach(() => {
    es = []
    vi.stubGlobal('EventSource', vi.fn().mockImplementation(function () {
      const inst = { onmessage: null as Listener, close: vi.fn() }
      es.push(inst)
      return inst
    }))
  })
  afterEach(() => { vi.restoreAllMocks() })

  const emit = (role: string, content: string) => act(() => {
    for (const inst of es) {
      inst.onmessage?.({ data: JSON.stringify({ type: 'task_message', task_id: 't1', role, content }) } as MessageEvent)
    }
  })

  function useHarness() {
    const open = makeTask({ id: 't1', status: 'running', created_at: '' })
    const [tasks, setTasks] = useState<Task[]>([open])
    // The open task: the hook routes messages through its updater.
    const [, setSelectedTask] = useState<Task | null>(open)
    const [items, setItems] = useState<ConversationItem[]>([])
    useSSE({
      selectedTaskId: 't1', appView: 'tasks', selectedStoreId: null, showAllTasks: true,
      setTasks, setSelectedTask, setSteps: vi.fn(), setScreenshots: vi.fn(),
      setAgentMessages: vi.fn(), setTodoItems: vi.fn(), setConversationItems: setItems,
      setPendingQuestions: vi.fn(), setSelectedAnswers: vi.fn(), setOtherInputs: vi.fn(),
      setShowOtherInput: vi.fn(), setLogs: vi.fn(), questionBannerRef: { current: null },
      setScheduleTasks: vi.fn(), loadScheduleTasks: vi.fn(), loadSchedules: vi.fn(), loadTasks: vi.fn(),
    })
    return { tasks, items }
  }

  it('the result replaces the same text just shown as a message', () => {
    const { result } = renderHook(() => useHarness())
    emit('assistant', ANSWER)
    emit('result', ANSWER)
    expect(shape(result.current.items)).toEqual([`result:${ANSWER}`])
  })

  it('a second turn\'s result is a card too, shown once', () => {
    const { result } = renderHook(() => useHarness())
    emit('assistant', 'a1')
    emit('result', 'a1')
    emit('user', 'q2')
    emit('assistant', 'a2')
    emit('result', 'a2')
    expect(shape(result.current.items)).toEqual(['result:a1', 'user_message', 'result:a2'])
  })
})
