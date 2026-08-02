/**
 * Unit tests for buildConversationItems (lib/conversation) — the pure
 * message+task → conversation-stream rebuild extracted from App.
 */
import { describe, it, expect } from 'vitest'
import { buildConversationItems } from '../lib/conversation'
import type { Task } from '../types'

const task = (over: Partial<Task> = {}): Task =>
  ({ id: 't1', status: 'completed', plan_mode: false, ...over }) as Task

describe('buildConversationItems', () => {
  it('maps roles to the right item types', () => {
    const items = buildConversationItems(
      [
        { role: 'user', content: 'hi' },
        { role: 'assistant', content: 'yo' },
        { role: 'thinking', content: 'hmm' },
      ],
      task(),
    )
    expect(items.map(i => i.type)).toEqual([
      'user_message',
      'agent_message',
      'thinking',
    ])
  })

  it('every turn keeps its own result, where it was produced', () => {
    // Only the first used to qualify as a result; later ones were demoted
    // to plain assistant messages, so on a multi-turn task each answer
    // ended up somewhere other than under the question that asked for it.
    const items = buildConversationItems(
      [
        { role: 'result', content: 'first' },
        { role: 'result', content: 'second' },
      ],
      task(),
    )
    expect(items.map(i => i.type)).toEqual(['result', 'result'])
  })

  it('task.result is authoritative — refreshes a persisted result card', () => {
    const items = buildConversationItems(
      [{ role: 'result', content: 'transcript snippet' }],
      task({ result: 'canonical result' }),
    )
    const finals = items.filter(i => i.type === 'result')
    expect(finals).toHaveLength(1)
    expect(finals[0].result).toBe('canonical result')
  })

  it('a two-turn task reads in order: ask, answer, ask, answer', () => {
    // The journey that exposed this. A seller asks for today's revenue,
    // gets it, then asks for an ad review in the same task. `task.result`
    // used to refresh the FIRST result slot, which drew turn 2's answer —
    // the ad-audit console — between the two questions, reading as though
    // it answered "how much did we sell today?", while turn 1's own
    // answer was displaced to the bottom of the thread as loose prose.
    const items = buildConversationItems(
      [
        { role: 'user', content: 'how much did we sell today?' },
        { role: 'result', content: 'SAR 77.98, 2 units' },
        { role: 'user', content: 'now review the socks ads' },
        { role: 'result', content: 'transcript snippet' },
      ],
      task({ result: 'THE AD AUDIT REPORT' }),
    )
    expect(
      items.map(i =>
        i.type === 'result' ? `result:${i.result}` : `${i.type}:${i.message?.content}`,
      ),
    ).toEqual([
      'user_message:how much did we sell today?',
      'result:SAR 77.98, 2 units',
      'user_message:now review the socks ads',
      'result:THE AD AUDIT REPORT',
    ])
  })

  it('the refreshed result keeps its own timestamp, not the rebuild time', () => {
    // The audit console binds a result to the declaration in force when
    // it landed; stamping "now" here would bind by rebuild time instead.
    const items = buildConversationItems(
      [{ role: 'result', content: 'snippet', created_at: '2026-08-02T10:00:00Z' }],
      task({ result: 'canonical' }),
    )
    const final = items.find(i => i.type === 'result')
    expect(final?.timestamp).toBe('2026-08-02T10:00:00Z')
  })

  it('adds an execution separator for a planned task in execute phase', () => {
    const items = buildConversationItems([], task({ plan: '1. do it', status: 'running' }))
    expect(items.some(i => i.type === 'execution_separator')).toBe(true)
  })

  it('skips malformed tool_use JSON without throwing', () => {
    const items = buildConversationItems(
      [{ role: 'tool_use', content: 'not json{' }],
      task(),
    )
    expect(items).toHaveLength(0)
  })

  it('reconstructs a generated image so it re-renders on reload', () => {
    const items = buildConversationItems(
      [{
        role: 'generated_image',
        content: JSON.stringify({
          path: 'generated_images/main.png',
          url: '/api/tasks/t1/files/generated_images/main.png',
          prompt: 'white bg', model: 'nano-banana-pro', kind: 'main',
        }),
      }],
      task(),
    )
    expect(items).toHaveLength(1)
    expect(items[0].type).toBe('generated_image')
    expect(items[0].generatedImage?.url)
      .toBe('/api/tasks/t1/files/generated_images/main.png')
    expect(items[0].generatedImage?.path).toBe('generated_images/main.png')
  })

  it('skips malformed generated_image JSON without throwing', () => {
    const items = buildConversationItems(
      [{ role: 'generated_image', content: 'not json{' }],
      task(),
    )
    expect(items).toHaveLength(0)
  })
})
