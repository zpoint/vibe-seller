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
  | 'agent' | 'todo' | 'skill' | 'tools' | 'ask' | 'plan' | 'mcp' | 'other'

export interface Step {
  category: Category
  /** What this one call is about: a file name, a description, a title. */
  target: string
  /** MCP server label (`Vibe Seller`), for `mcp` steps. */
  server?: string
  /**
   * A Vibe Seller tool the UI can name in words (`ads`, `emailSend`), so
   * the line says "Called the Ads API" rather than "Used Vibe Seller".
   */
  family?: string
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

type Input = Record<string, unknown> | undefined

/** `GET /sync · AE` — what one Ads API call asked for. */
function adsTarget(input: Input): string {
  const path = str(input, 'path')
  if (!path) return ''
  const method = (str(input, 'method') || 'GET').toUpperCase()
  const mkt = str(input, 'marketplace')
  return `${method} ${path}${mkt ? ` · ${mkt}` : ''}`
}

/**
 * The in-task Vibe Seller tools (`app/mcp_server.py`), named by what they
 * do. A workspace write is a file edit; the rest get a family whose words
 * live under `activity.tool.<family>`. A tool missing here still shows as
 * `Vibe Seller · <Title>`.
 */
const VS_TOOLS: Record<string, { family: string; target?: (i: Input) => string }> = {
  ads_call: { family: 'ads', target: adsTarget },
  email_info: { family: 'emailRead' },
  get_new_emails: { family: 'emailRead' },
  sync_email_now: { family: 'emailRead' },
  send_email: { family: 'emailSend', target: i => clip(str(i, 'subject'), 50) },
  declare_ad_task: {
    family: 'declareAd',
    target: i => [str(i, 'kind'), str(i, 'scope')].filter(Boolean).join(' · '),
  },
  declare_report_run: { family: 'declareReport', target: i => str(i, 'month') },
  create_task: { family: 'subtask', target: i => clip(str(i, 'title', 'description'), 50) },
  list_tasks: { family: 'tasks' },
  list_stores: { family: 'stores' },
  set_task_result: { family: 'result' },
  set_task_error: { family: 'error', target: i => clip(str(i, 'error'), 50) },
  send_chat_message: { family: 'chat', target: i => clip(str(i, 'content'), 40) },
  send_chat_file: { family: 'chat', target: i => basename(str(i, 'path')) },
  generate_image: { family: 'image', target: i => str(i, 'output_name') || clip(str(i, 'prompt'), 40) },
  get_schedule_state: { family: 'scheduleState', target: i => str(i, 'key') },
  set_schedule_state: { family: 'scheduleState', target: i => str(i, 'key') },
  list_cron_jobs: { family: 'cron' },
  add_cron_job: { family: 'cron', target: i => clip(str(i, 'task_title'), 40) },
  list_skills: { family: 'skills' },
  save_skill: { family: 'skills', target: i => str(i, 'slug') },
  import_skill: { family: 'skills', target: i => str(i, 'slug') },
}

export function describeStep(tool: string, input?: Record<string, unknown>): Step {
  const s = (category: Category, target: string, server?: string, family?: string): Step =>
    ({ category, target, server, tool, input, family })
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
    case 'ToolSearch':
      return s('tools', '')
    case 'AskUserQuestion':
      return s('ask', '')
    case 'ExitPlanMode':
      return s('plan', '')
    case 'Skill':
      return s('skill', str(input, 'skill', 'name', 'command'))
  }
  const parts = tool.split('__')
  if (parts[0] === 'mcp' && parts.length >= 3) {
    if (parts[1] === 'vibe-seller') {
      const name = parts.slice(2).join('__').replace(/^vibe_seller_/, '')
      if (name === 'write_workspace_file' || name === 'delete_workspace_lines') {
        return s('edit', basename(str(input, 'path')))
      }
      const known = VS_TOOLS[name]
      if (known) {
        return s('mcp', known.target?.(input) || '', serverLabel(parts[1]), known.family)
      }
    }
    return s('mcp', toolTitle(tool), serverLabel(parts[1]))
  }
  return s('other', tool)
}

/** Steps that read the same in a summary share a key. */
function groupKey(step: Step): string {
  if (step.category === 'mcp') return `mcp:${step.family || step.server}`
  if (step.category === 'other') return `other:${step.target}`
  return step.category
}

/** A named Vibe Seller tool, one call: "Ads API: GET /sync · AE". */
function familyLabel(step: Step, kind: 'row' | 'live', t: T): string {
  const key = `activity.tool.${step.family}`
  return step.target
    ? t(`${key}.${kind}`, { target: step.target })
    : t(`${key}.${kind}Bare`)
}

/** Present tense, for the live line: "Running Install deps". */
export function liveLabel(step: Step, t: T): string {
  if (step.family) return familyLabel(step, 'live', t)
  const target = step.category === 'mcp'
    ? `${step.server} · ${step.target}`
    : step.target
  return t(`activity.live.${step.category}`, { target })
}

/** "read notes.md" / "read 3 files" — one category's share of a run. */
function segment(cat: Category, steps: Step[], t: T): string {
  const n = steps.length
  const family = steps[0].family
  if (family) {
    // "called the Ads API: GET /sync" / "called the Ads API 3 times"
    return n === 1 && steps[0].target
      ? t(`activity.tool.${family}.doneTarget`, { target: steps[0].target })
      : counted(t, `activity.tool.${family}.done`, n)
  }
  if (cat === 'mcp') {
    // One segment per server: "used Vibe Seller".
    return counted(t, 'activity.done.mcp', n, { target: steps[0].server })
  }
  if (cat === 'other') {
    return counted(t, 'activity.done.other', n, { target: steps[0].target })
  }
  return counted(t, `activity.done.${cat}`, n, { target: steps[0].target })
}

/**
 * `base_one` for a single step, `base_other` otherwise, picked here rather
 * than by i18next: Chinese has no singular plural form, so `count: 1`
 * would read "提交结果 1 次" instead of "提交结果".
 */
function counted(t: T, base: string, n: number, opts: Record<string, unknown> = {}): string {
  return t(`${base}_${n === 1 ? 'one' : 'other'}`, { ...opts, count: n })
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
    const key = groupKey(step)
    if (!groups.has(key)) {
      groups.set(key, { cat: step.category, steps: [] })
      order.push(key)
    }
    groups.get(key)!.steps.push(step)
  }
  if (order.length === 0) return counted(t, 'activity.thought', thoughts)
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
  | { kind: 'steps'; key: string; category: Category; items: ConversationItem[]; steps: Step[] }

/** Rows inside an expanded run: consecutive calls of one kind merge. */
export function buildRows(items: ConversationItem[]): Row[] {
  const rows: Row[] = []
  for (const i of items) {
    if (i.type !== 'tool_call' || !i.toolCall) {
      rows.push({ kind: 'thought', item: i })
      continue
    }
    const step = describeStep(i.toolCall.tool, i.toolCall.input)
    const key = groupKey(step)
    const prev = rows[rows.length - 1]
    if (prev && prev.kind === 'steps' && prev.key === key && step.category !== 'other') {
      prev.items.push(i)
      prev.steps.push(step)
    } else {
      rows.push({ kind: 'steps', key, category: step.category, items: [i], steps: [step] })
    }
  }
  return rows
}

/** "GET /sync · AE ×2, GET /profiles" — the targets of a merged row. */
function targetList(steps: Step[], t: T, max = 2): string {
  const counts = new Map<string, number>()
  for (const s of steps) {
    if (s.target) counts.set(s.target, (counts.get(s.target) || 0) + 1)
  }
  const names = [...counts].map(([k, c]) => (c > 1 ? `${k} ×${c}` : k))
  return names.slice(0, max).join(t('activity.sep')) +
    (names.length > max ? t('activity.andMore', { count: names.length - max }) : '')
}

/** Row header: "Read notes.md, CATALOG.md and 1 more" / "Ads API: GET /sync ×2". */
export function rowLabel(row: Extract<Row, { kind: 'steps' }>, t: T): string {
  const n = row.steps.length
  const first = row.steps[0]
  if (n === 1) return stepLabel(first, t)
  if (first.family) {
    const list = targetList(row.steps, t)
    return list
      ? t(`activity.tool.${first.family}.row`, { target: list })
      : capitalize(counted(t, `activity.tool.${first.family}.done`, n))
  }
  if (row.category === 'mcp') {
    // "Vibe Seller · Read knowledge, Workspace file ×3"
    return `${first.server} · ${targetList(row.steps, t, 4)}`
  }
  const list = targetList(row.steps, t)
  if (list && !['todo', 'tools', 'ask', 'plan'].includes(row.category)) {
    return t(`activity.row.${row.category}`, { target: list })
  }
  return capitalize(counted(t, `activity.done.${row.category}`, n, { target: list }))
}

/** First line of a thought, as the agent's own narration. */
export function thoughtLine(content: string): string {
  const line = content.trim().split('\n').find(l => l.trim()) || ''
  return clip(line.replace(/^[#*>\-\s]+/, ''), 120)
}

/** A single step named on its own: "Read notes.md", "Ran Install deps". */
export function stepLabel(step: Step, t: T): string {
  if (step.family) return familyLabel(step, 'row', t)
  if (step.category === 'mcp') return `${step.server} · ${step.target}`
  return t(`activity.row.${step.category}`, { target: step.target || step.tool })
}
