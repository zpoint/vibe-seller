/**
 * Plain-language summaries of the agent's working steps (tool calls and
 * thinking), for the collapsed activity lines in the conversation.
 *
 * A run of steps reads as what was done — "Read notes.md, ran 2 commands,
 * used Vibe Seller" — and each row inside names the step by what it is
 * for: the Bash `description` the agent wrote, the file it read, the
 * Vibe Seller tool it called.
 */
import type { ConversationItem } from '../types'

type T = (key: string, opts?: Record<string, unknown>) => string

export type Category =
  | 'read' | 'search' | 'edit' | 'run' | 'browse' | 'web'
  | 'agent' | 'todo' | 'skill' | 'ask' | 'plan' | 'mcp' | 'other'

export interface Step {
  category: Category
  /** What this one call is about: a file name, a description, a title. */
  target: string
  /** MCP server label (`Vibe Seller`), for `mcp` steps. */
  server?: string
  tool: string
  input?: Record<string, unknown>
}

function str(input: Record<string, unknown> | undefined, ...keys: string[]): string {
  if (!input) return ''
  for (const k of keys) {
    const v = input[k]
    if (typeof v === 'string' && v.trim()) return v.trim()
  }
  return ''
}

function basename(p: string): string {
  const parts = p.split(/[\\/]/).filter(Boolean)
  return parts[parts.length - 1] || p
}

function clip(s: string, n = 60): string {
  const one = s.replace(/\s+/g, ' ').trim()
  return one.length > n ? one.slice(0, n) + '…' : one
}

function titleCase(s: string): string {
  const w = s.replace(/[_-]+/g, ' ').trim()
  return w.charAt(0).toUpperCase() + w.slice(1)
}

/** `vibe-seller` → `Vibe Seller`. */
function serverLabel(server: string): string {
  return server
    .split(/[-_]+/)
    .filter(Boolean)
    .map(w => w.charAt(0).toUpperCase() + w.slice(1))
    .join(' ')
}

/** `mcp__vibe-seller__vibe_seller_read_knowledge` → `Read knowledge`. */
export function toolTitle(tool: string): string {
  const parts = tool.split('__')
  if (parts[0] === 'mcp' && parts.length >= 3) {
    const name = parts.slice(2).join('__')
    const server = parts[1].replace(/-/g, '_')
    return titleCase(name.replace(new RegExp(`^${server}_`), ''))
  }
  return tool
}

export function describeStep(tool: string, input?: Record<string, unknown>): Step {
  const s = (category: Category, target: string, server?: string): Step =>
    ({ category, target, server, tool, input })
  switch (tool) {
    case 'Read':
      return s('read', basename(str(input, 'file_path', 'path')))
    case 'Grep':
    case 'Glob':
      return s('search', clip(str(input, 'pattern'), 40))
    case 'Edit':
    case 'Write':
    case 'MultiEdit':
    case 'NotebookEdit':
      return s('edit', basename(str(input, 'file_path', 'notebook_path')))
    case 'Bash': {
      const cmd = str(input, 'command')
      const desc = str(input, 'description')
      const cat = /^\s*browser-use\b/.test(cmd) ? 'browse' : 'run'
      return s(cat, desc || clip(cmd))
    }
    case 'WebFetch':
      return s('web', str(input, 'url'))
    case 'WebSearch':
      return s('web', str(input, 'query'))
    case 'Task':
    case 'Agent':
      return s('agent', str(input, 'description', 'subagent_type'))
    case 'TodoWrite':
      return s('todo', '')
    case 'AskUserQuestion':
      return s('ask', '')
    case 'ExitPlanMode':
      return s('plan', '')
    case 'Skill':
      return s('skill', str(input, 'skill', 'name', 'command'))
  }
  const parts = tool.split('__')
  if (parts[0] === 'mcp' && parts.length >= 3) {
    return s('mcp', toolTitle(tool), serverLabel(parts[1]))
  }
  return s('other', tool)
}

/** Present tense, for the live line: "Running Install deps". */
export function liveLabel(step: Step, t: T): string {
  const target = step.category === 'mcp'
    ? `${step.server} · ${step.target}`
    : step.target
  return t(`activity.live.${step.category}`, { target })
}

/** "read notes.md" / "read 3 files" — one category's share of a run. */
function segment(cat: Category, steps: Step[], t: T): string {
  const n = steps.length
  if (cat === 'mcp') {
    // One segment per server: "used Vibe Seller".
    return t('activity.done.mcp', { target: steps[0].server, count: n })
  }
  if (cat === 'other') {
    return t('activity.done.other', { target: steps[0].target, count: n })
  }
  return t(`activity.done.${cat}`, { target: steps[0].target, count: n })
}

function capitalize(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1)
}

/** Header for a whole run: "Read notes.md, ran 2 commands, used Vibe Seller". */
export function summarizeRun(items: ConversationItem[], t: T): string {
  const order: string[] = []
  const groups = new Map<string, { cat: Category; steps: Step[] }>()
  let thoughts = 0
  for (const i of items) {
    if (i.type !== 'tool_call' || !i.toolCall) {
      thoughts++
      continue
    }
    const step = describeStep(i.toolCall.tool, i.toolCall.input)
    const key = step.category === 'mcp' ? `mcp:${step.server}`
      : step.category === 'other' ? `other:${step.target}`
      : step.category
    if (!groups.has(key)) {
      groups.set(key, { cat: step.category, steps: [] })
      order.push(key)
    }
    groups.get(key)!.steps.push(step)
  }
  if (order.length === 0) return t('activity.thought', { count: thoughts })
  const segs = order.map(k => segment(groups.get(k)!.cat, groups.get(k)!.steps, t))
  const shown = segs.slice(0, 3)
  let text = shown.join(t('activity.sep'))
  if (segs.length > 3) {
    text += t('activity.more', { count: segs.length - 3 })
  }
  return capitalize(text)
}

export type Row =
  | { kind: 'thought'; item: ConversationItem }
  | { kind: 'steps'; category: Category; server?: string; items: ConversationItem[]; steps: Step[] }

/** Rows inside an expanded run: consecutive calls of one kind merge. */
export function buildRows(items: ConversationItem[]): Row[] {
  const rows: Row[] = []
  for (const i of items) {
    if (i.type !== 'tool_call' || !i.toolCall) {
      rows.push({ kind: 'thought', item: i })
      continue
    }
    const step = describeStep(i.toolCall.tool, i.toolCall.input)
    const prev = rows[rows.length - 1]
    if (
      prev && prev.kind === 'steps' &&
      prev.category === step.category && prev.server === step.server &&
      step.category !== 'other'
    ) {
      prev.items.push(i)
      prev.steps.push(step)
    } else {
      rows.push({
        kind: 'steps', category: step.category, server: step.server,
        items: [i], steps: [step],
      })
    }
  }
  return rows
}

/** Row header: "Read notes.md, CATALOG.md and 1 more" / "Ran Install deps". */
export function rowLabel(row: Extract<Row, { kind: 'steps' }>, t: T): string {
  const n = row.steps.length
  if (n === 1) return stepLabel(row.steps[0], t)
  if (row.category === 'mcp') {
    // "Vibe Seller · Read knowledge, Workspace file ×3"
    const counts = new Map<string, number>()
    for (const s of row.steps) counts.set(s.target, (counts.get(s.target) || 0) + 1)
    const names = [...counts].map(([k, c]) => (c > 1 ? `${k} ×${c}` : k))
    return `${row.server} · ${names.join(t('activity.sep'))}`
  }
  const targets = [...new Set(row.steps.map(s => s.target).filter(Boolean))]
  const list = targets.slice(0, 2).join(t('activity.sep')) +
    (targets.length > 2 ? t('activity.andMore', { count: targets.length - 2 }) : '')
  if (['read', 'edit', 'search', 'web'].includes(row.category)) {
    return t(`activity.row.${row.category}`, { target: list || row.steps[0].tool, count: n })
  }
  return capitalize(t(`activity.done.${row.category}`, { target: list, count: n }))
}

/** First line of a thought, as the agent's own narration. */
export function thoughtLine(content: string): string {
  const line = content.trim().split('\n').find(l => l.trim()) || ''
  return clip(line.replace(/^[#*>\-\s]+/, ''), 120)
}

/** A single step named on its own: "Read notes.md", "Ran Install deps". */
export function stepLabel(step: Step, t: T): string {
  if (step.category === 'mcp') return `${step.server} · ${step.target}`
  return t(`activity.row.${step.category}`, { target: step.target || step.tool })
}
