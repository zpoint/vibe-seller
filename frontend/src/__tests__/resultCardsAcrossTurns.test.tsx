/**
 * A task answered over several turns shows one result card per turn,
 * however often the thread re-renders. Each live turn records its
 * result (a provisional card) and then ends with its own result, in that
 * order, as the server streams them.
 */
import { describe, it, expect } from 'vitest'
import { screen } from '@testing-library/react'
import { conversationStreamElement, makeConversationItem, renderConversationStream } from '../test/helpers'
import { addLiveResult } from '../lib/conversation'
import type { ConversationItem } from '../types'

const res = (result: string) =>
  makeConversationItem('result', { result }) as ConversationItem & { type: 'result' }
const said = (content: string) =>
  makeConversationItem('agent_message', { message: { role: 'assistant', content } })
const asked = (content: string) =>
  makeConversationItem('user_message', { message: { role: 'user', content } })
const ran = () =>
  makeConversationItem('tool_call', { toolCall: { tool: 'Bash', input: { command: 'ls' } } })

/** One live turn: the recorded result, the reply, then the turn result. */
function turn(items: ConversationItem[], n: number): ConversationItem[] {
  items = addLiveResult(items, res(`recorded ${n}`), true)
  items = [...items, said(`reply ${n}`)]
  return addLiveResult(items, res(`reply ${n}`), false)
}

describe('result cards across turns', () => {
  it('gives every card its own identity', () => {
    let items: ConversationItem[] = []
    for (let n = 1; n <= 3; n++) items = turn([...items, asked(`ask ${n}`)], n)
    const ids = items.map(i => i.id)
    expect(new Set(ids).size).toBe(ids.length)
  })

  it('renders one card per turn through every live update', () => {
    let items: ConversationItem[] = []
    const view = renderConversationStream({ items })
    const show = (next: ConversationItem[]) => {
      items = next
      view.rerender(conversationStreamElement({ items }))
    }
    for (let n = 1; n <= 3; n++) {
      show([...items, asked(`ask ${n}`)])
      // the plan arrives; earlier plans are no longer current
      show([...items.map(i => (i.type === 'plan' ? { ...i, plan: { ...i.plan!, isCurrent: false } } : i)),
        makeConversationItem('plan', { plan: { version: n, content: `plan ${n}`, isCurrent: true } })])
      // the turn's tool calls stream in one at a time
      for (let k = 0; k < 3; k++) show([...items, ran()])
      show(addLiveResult(items, res(`recorded ${n}`), true))
      show([...items, said(`reply ${n}`)])
      show(addLiveResult(items, res(`reply ${n}`), false))
    }
    for (let n = 1; n <= 3; n++) {
      expect(screen.getAllByText(`recorded ${n}`)).toHaveLength(1)
    }
  })
})
