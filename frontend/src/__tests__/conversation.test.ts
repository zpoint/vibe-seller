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

  it('first result becomes a result card, later results demote to messages', () => {
    const items = buildConversationItems(
      [
        { role: 'result', content: 'first' },
        { role: 'result', content: 'second' },
      ],
      task(),
    )
    expect(items[0].type).toBe('result')
    expect(items[1].type).toBe('agent_message')
  })

  it('task.result is authoritative — replaces a persisted result card', () => {
    const items = buildConversationItems(
      [{ role: 'result', content: 'transcript snippet' }],
      task({ result: 'canonical result' }),
    )
    const finals = items.filter(i => i.type === 'result')
    expect(finals).toHaveLength(1)
    expect(finals[0].result).toBe('canonical result')
    // the demoted transcript stays visible as an agent message
    expect(items.some(i => i.type === 'agent_message' && i.message?.content === 'transcript snippet')).toBe(true)
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
