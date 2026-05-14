/**
 * Conversation flow rendering tests for new item types:
 * tool calls, thinking blocks, plan versioning, working indicator.
 */
import { describe, it, expect, vi } from 'vitest'
import { screen } from '@testing-library/react'
import {
  makeConversationItem,
  makePlan,
  renderConversationStream,
} from '../test/helpers'
import type { ConversationItem } from '../types'

describe('Tool call rendering', () => {
  it('renders single tool call with tool name and file path', () => {
    const items = [makeConversationItem('tool_call', {
      toolCall: { tool: 'Read', input: { file_path: 'app/models.py' } },
    })]
    renderConversationStream({ items })
    expect(screen.getByText(/Read/)).toBeInTheDocument()
    expect(screen.getByText(/models\.py/)).toBeInTheDocument()
  })

  it('groups consecutive tool calls into expandable block', () => {
    const items = [
      makeConversationItem('tool_call', {
        toolCall: { tool: 'Read', input: { file_path: 'a.py' } },
      }),
      makeConversationItem('tool_call', {
        toolCall: { tool: 'Grep', input: { pattern: 'class Task' } },
      }),
      makeConversationItem('tool_call', {
        toolCall: { tool: 'Read', input: { file_path: 'b.py' } },
      }),
    ]
    renderConversationStream({ items })
    expect(screen.getByText(/3 tool calls/)).toBeInTheDocument()
  })

  it('single tool call between other items is not grouped', () => {
    const items = [
      makeConversationItem('agent_message', {
        message: { role: 'assistant', content: 'hi' },
      }),
      makeConversationItem('tool_call', {
        toolCall: { tool: 'Read', input: { file_path: 'a.py' } },
      }),
      makeConversationItem('agent_message', {
        message: { role: 'assistant', content: 'done' },
      }),
    ]
    renderConversationStream({ items })
    // Should show "1 tool call" (single item group) not "N tool calls"
    expect(screen.queryByText(/\d+ tool calls/)).not.toBeInTheDocument()
    expect(screen.getByText(/Read/)).toBeInTheDocument()
  })
})

describe('Thinking block rendering', () => {
  it('renders streaming thinking with content preview', () => {
    const items = [makeConversationItem('thinking', {
      thinking: { content: 'Let me analyze the code...', isStreaming: true },
    })]
    renderConversationStream({
      items,
      task: { status: 'designing' },
    })
    expect(screen.getByText(/Thinking/)).toBeInTheDocument()
    expect(screen.getByText(/analyze the code/)).toBeInTheDocument()
  })

  it('renders finalized thinking as collapsed', () => {
    const items = [makeConversationItem('thinking', {
      thinking: { content: 'Full reasoning here...', isStreaming: false },
    })]
    renderConversationStream({ items })
    expect(screen.getByText(/Thinking/)).toBeInTheDocument()
  })
})

describe('Plan versioning', () => {
  it('renders old plan as compact one-liner, current plan fully', () => {
    const items = [
      makeConversationItem('plan', {
        plan: makePlan({ version: 1, isCurrent: false }),
      }),
      makeConversationItem('user_message', {
        message: { role: 'user', content: 'change X' },
      }),
      makeConversationItem('plan', {
        plan: makePlan({ version: 2, isCurrent: true }),
      }),
    ]
    renderConversationStream({
      items,
      task: { status: 'planned', plan_mode: true, schedule_id: null },
    })
    // Superseded plan shows compact marker with "superseded"
    expect(screen.getByText(/v1.*superseded/i)).toBeInTheDocument()
    // Current plan shows version tabs (v1, v2)
    expect(screen.getByRole('button', { name: 'v2' })).toBeInTheDocument()
    // User message between
    expect(screen.getByText('change X')).toBeInTheDocument()
  })

  it('shows "Revised from" badge on v2+ plans', () => {
    const items = [makeConversationItem('plan', {
      plan: makePlan({ version: 2, isCurrent: true }),
    })]
    renderConversationStream({
      items,
      task: { status: 'planned', plan_mode: true, schedule_id: null },
    })
    expect(screen.getByText(/Revised from v1/)).toBeInTheDocument()
  })
})

describe('Request Changes button', () => {
  it('shows alongside Confirm when planned + review', () => {
    const onRequestChanges = vi.fn()
    const items = [makeConversationItem('plan', {
      plan: makePlan({ version: 1, isCurrent: true }),
    })]
    renderConversationStream({
      items,
      task: { status: 'planned', plan_mode: true, schedule_id: null },
      onRequestChanges,
    })
    expect(screen.getByText(/Request Changes/i)).toBeInTheDocument()
    expect(screen.getByText(/Confirm/i)).toBeInTheDocument()
  })

  it('hidden when not in planned state', () => {
    const items = [makeConversationItem('plan', {
      plan: makePlan({ version: 1, isCurrent: true }),
    })]
    renderConversationStream({
      items,
      task: { status: 'running' },
    })
    expect(screen.queryByText(/Request Changes/i)).not.toBeInTheDocument()
  })
})

describe('Full conversation flow', () => {
  it('renders plan→feedback→replan→execute→result', () => {
    const items: ConversationItem[] = [
      makeConversationItem('thinking', {
        thinking: { content: 'analyzing...', isStreaming: false },
      }),
      makeConversationItem('tool_call', {
        toolCall: { tool: 'Read', input: { file_path: 'a.py' } },
      }),
      makeConversationItem('tool_call', {
        toolCall: { tool: 'Grep', input: { pattern: 'foo' } },
      }),
      makeConversationItem('plan', {
        plan: makePlan({ version: 1, isCurrent: false }),
      }),
      makeConversationItem('user_message', {
        message: { role: 'user', content: 'change approach' },
      }),
      makeConversationItem('thinking', {
        thinking: { content: 'revising...', isStreaming: false },
      }),
      makeConversationItem('plan', {
        plan: makePlan({
          version: 2,
          content: '## Revised\n1. New step',
          isCurrent: true,
        }),
      }),
      makeConversationItem('execution_separator'),
      makeConversationItem('tool_call', {
        toolCall: { tool: 'Edit', input: { file_path: 'b.py' } },
      }),
      makeConversationItem('result', { result: 'Done!' }),
    ]
    renderConversationStream({
      items,
      task: { status: 'completed' },
    })

    // Grouped tool calls (2 consecutive before plan v1)
    expect(screen.getByText(/2 tool calls/)).toBeInTheDocument()
    // User feedback
    expect(screen.getByText('change approach')).toBeInTheDocument()
    // New plan content
    expect(screen.getByText('New step')).toBeInTheDocument()
    // Execution separator
    expect(screen.getByText(/execution started/i)).toBeInTheDocument()
    // Result
    expect(screen.getByText('Done!')).toBeInTheDocument()
  })
})

describe('Working indicator', () => {
  it('shows when active and no streaming items', () => {
    renderConversationStream({
      items: [makeConversationItem('tool_call', {
        toolCall: { tool: 'Read', input: {} },
      })],
      task: { status: 'designing' },
      isActive: true,
    })
    expect(screen.getByText(/working/i)).toBeInTheDocument()
  })

  it('hidden when streaming thinking is active', () => {
    renderConversationStream({
      items: [makeConversationItem('thinking', {
        thinking: { content: '...', isStreaming: true },
      })],
      task: { status: 'designing' },
      isActive: true,
    })
    expect(screen.queryByText(/working/i)).not.toBeInTheDocument()
  })

  it('hidden when not active', () => {
    renderConversationStream({
      items: [],
      task: { status: 'completed' },
      isActive: false,
    })
    expect(screen.queryByText(/working/i)).not.toBeInTheDocument()
  })
})

describe('Plan-skip flow', () => {
  it('renders tool calls and result without plan card', () => {
    const items: ConversationItem[] = [
      makeConversationItem('tool_call', {
        toolCall: { tool: 'Bash', input: { command: 'echo hi' } },
      }),
      makeConversationItem('result', { result: 'Quick result' }),
    ]
    renderConversationStream({
      items,
      task: { status: 'completed' },
    })
    expect(screen.queryByText(/Current Plan/)).not.toBeInTheDocument()
    expect(screen.getByText('Quick result')).toBeInTheDocument()
  })
})
