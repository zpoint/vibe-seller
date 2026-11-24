/**
 * Conversation flow rendering tests for new item types:
 * tool calls, thinking blocks, plan versioning, working indicator.
 */
import { describe, it, expect, vi } from 'vitest'
import { cleanup, fireEvent, screen } from '@testing-library/react'
import {
  makeConversationItem,
  makePlan,
  renderConversationStream,
} from '../test/helpers'
import type { ConversationItem } from '../types'
import i18next from 'i18next'
import zhTranslation from '../i18n/locales/zh/translation.json'
import { describeStep, summarizeRun, toolTitle } from '../lib/activity'

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
    renderConversationStream({ items, isActive: false })
    expect(screen.getByText('Read 2 files, searched for class Task')).toBeInTheDocument()
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
    // Should show "1 action" (single item group) not "N actions"
    expect(screen.queryByText(/\d+ actions/)).not.toBeInTheDocument()
    expect(screen.getByText(/Read/)).toBeInTheDocument()
  })
})

describe('Agent activity folding', () => {
  const thinking = (content: string, isStreaming = false) =>
    makeConversationItem('thinking', { thinking: { content, isStreaming } })
  const tool = (t: string, input: Record<string, unknown>) =>
    makeConversationItem('tool_call', { toolCall: { tool: t, input } })

  it('folds interleaved thinking and tool calls into one line that says what was done', () => {
    const items = [
      thinking('First read the store notes'),
      tool('Read', { file_path: 'stores/acme/notes.md' }),
      thinking('Now declare the ad task'),
      tool('mcp__vibe-seller__vibe_seller_declare_ad_task', { kind: 'audit', scope: 'campaigns' }),
      tool('mcp__vibe-seller__vibe_seller_ads_call', { path: '/sync', marketplace: 'AE' }),
      tool('mcp__vibe-seller__vibe_seller_ads_call', { path: '/sync', marketplace: 'SA' }),
      thinking('Open the ad console'),
      tool('Bash', { command: 'browser-use <<PY\nnew_tab("x")\nPY', description: 'Open the ads console' }),
      tool('Bash', { command: 'ls -lt downloads/', description: 'List downloaded reports' }),
      makeConversationItem('agent_message', {
        message: { role: 'assistant', content: 'Found the data source.' },
      }),
      tool('Read', { file_path: 'a.md' }),
      tool('Read', { file_path: 'b.md' }),
    ]
    renderConversationStream({ items, task: { status: 'completed' }, isActive: false })

    expect(screen.getAllByTestId('activity-group')).toHaveLength(2)
    expect(screen.getByText(
      'Read notes.md, declared the ad task (audit · campaigns), called the Ads API 2 times, and 2 more',
    )).toBeInTheDocument()
    // Collapsed: the steps themselves stay out of the way.
    expect(screen.queryByText(/First read the store notes/)).not.toBeInTheDocument()
    // The agent's message between runs stays readable.
    expect(screen.getByText('Found the data source.')).toBeInTheDocument()
    expect(screen.getByText('Read 2 files')).toBeInTheDocument()

    // Expanded: rows named by purpose, the agent's thoughts as narration.
    fireEvent.click(screen.getByText(/^Read notes\.md, declared the ad task/))
    expect(screen.getByText('First read the store notes')).toBeInTheDocument()
    expect(screen.getByText('Read notes.md')).toBeInTheDocument()
    // Each Vibe Seller tool is named for what it does, not "Vibe Seller".
    expect(screen.getByText('Declared the ad task: audit · campaigns')).toBeInTheDocument()
    expect(screen.getByText('Ads API: GET /sync · AE, GET /sync · SA')).toBeInTheDocument()
    expect(screen.getByText('Browser: Open the ads console')).toBeInTheDocument()
    expect(screen.getByText('Ran List downloaded reports')).toBeInTheDocument()

    // A row opens to the call behind it.
    fireEvent.click(screen.getByText('Ran List downloaded reports'))
    expect(screen.getByText('ls -lt downloads/')).toBeInTheDocument()
  })

  it('says what the agent is doing right now while the last run is live', () => {
    const items = [
      thinking('plan'),
      tool('Bash', { command: 'npm ci', description: 'Install dependencies' }),
    ]
    renderConversationStream({ items, isActive: true })
    expect(screen.getByText('Running Install dependencies')).toBeInTheDocument()
  })

  it('names the Vibe Seller call in progress and lists merged commands by purpose', () => {
    renderConversationStream({
      items: [thinking('sync'), tool('mcp__vibe-seller__vibe_seller_ads_call', { path: '/sync', marketplace: 'AE' })],
      isActive: true,
    })
    expect(screen.getByText('Calling the Ads API: GET /sync · AE')).toBeInTheDocument()

    cleanup()
    renderConversationStream({
      items: [
        thinking('look around'),
        tool('Bash', { command: 'ls', description: 'List reports' }),
        tool('Bash', { command: 'cat a', description: 'Show the notes' }),
      ],
      isActive: false,
    })
    fireEvent.click(screen.getByText('Ran 2 commands'))
    expect(screen.getByText('Ran List reports, Show the notes')).toBeInTheDocument()
  })

  it('reads naturally in Chinese, where one call is not "1 次"', async () => {
    const zh = i18next.createInstance()
    await zh.init({ lng: 'zh', resources: { zh: { translation: zhTranslation } } })
    const items = [
      tool('mcp__vibe-seller__vibe_seller_write_workspace_file', { path: 'stores/acme/notes.md' }),
      tool('mcp__vibe-seller__vibe_seller_ads_call', { path: '/sync', marketplace: 'AE' }),
      tool('mcp__vibe-seller__vibe_seller_ads_call', { path: '/sync', marketplace: 'SA' }),
      tool('mcp__vibe-seller__vibe_seller_set_task_result', { result: 'done' }),
    ]
    expect(summarizeRun(items, zh.t.bind(zh) as never)).toBe('编辑 notes.md，调用广告 API 2 次，提交结果')
  })

  it('says it is waiting on the user, not the raw question tool name', () => {
    const items = [
      thinking('need a login'),
      tool('AskUserQuestion', { questions: [] }),
    ]
    renderConversationStream({ items, isActive: true })
    expect(screen.getByText('Waiting for your answer')).toBeInTheDocument()
    expect(screen.queryByText(/AskUserQuestion/)).not.toBeInTheDocument()
  })

  it('streams the latest thought under a live collapsed run', () => {
    const items = [
      tool('Read', { file_path: 'a.md' }),
      thinking('checking the report\nlooking at today sales', true),
    ]
    renderConversationStream({ items, isActive: true })
    expect(screen.getByText('Thinking...')).toBeInTheDocument()
    expect(screen.getByText('looking at today sales')).toBeInTheDocument()
  })

  it('names steps by purpose, not by raw tool id', () => {
    expect(toolTitle('mcp__vibe-seller__vibe_seller_read_knowledge')).toBe('Read knowledge')
    expect(toolTitle('mcp__gws__sheets_read')).toBe('Sheets read')
    expect(describeStep('Bash', { command: 'ls -la' }).target).toBe('ls -la')
    expect(describeStep('Bash', { command: 'ls', description: 'List files' }).target).toBe('List files')
    expect(describeStep('Bash', { command: 'browser-use <<PY' }).category).toBe('browse')
    expect(describeStep('Skill', { skill: 'browser-harness' }).target).toBe('browser-harness')
    expect(describeStep('ToolSearch', { query: 'select:X' }).category).toBe('tools')
    expect(describeStep('Read', { file_path: 'C:\\Users\\a\\notes.md' }).target).toBe('notes.md')
    // A workspace write is a file edit; a tool the UI has no words for
    // still names its server and tool.
    const write = describeStep('mcp__vibe-seller__vibe_seller_write_workspace_file', { path: 'stores/acme/notes.md' })
    expect([write.category, write.target]).toEqual(['edit', 'notes.md'])
    const unknown = describeStep('mcp__vibe-seller__vibe_seller_read_knowledge', { path: 'x' })
    expect([unknown.category, unknown.server, unknown.target, unknown.family])
      .toEqual(['mcp', 'Vibe Seller', 'Read knowledge', undefined])
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

    // Thinking + 2 tool calls before plan v1 fold into one line
    expect(screen.getByText('Read a.py, searched for foo')).toBeInTheDocument()
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
