/**
 * Rebuild the conversation stream for an opened task from its persisted
 * messages + task state. Pure (no React / no fetch) so it's unit-tested
 * directly and keeps App.tsx lean. Extracted from loadTaskById.
 */
import type { ConversationItem, Task } from '../types'
import { NOTICE_ROLE, parseNotice } from './notice'

export interface RawMessage {
  role: string
  content: string
  created_at?: string
}

/**
 * Append a turn's result card. Claude Code's ``result`` event repeats
 * the turn's last assistant text, and both are persisted and streamed
 * (role ``assistant``, then role ``result``). One answer must render
 * once: when the item right before is that same text as a plain agent
 * message, the result card takes its place. Used by the history rebuild
 * and the live SSE path alike, so a reload and a live view agree.
 */
export function appendResult(
  items: ConversationItem[],
  item: ConversationItem & { type: 'result' },
): ConversationItem[] {
  const prev = items[items.length - 1]
  const same = prev?.type === 'agent_message'
    && (prev.message?.content ?? '').trim() === (item.result ?? '').trim()
  return same ? [...items.slice(0, -1), item] : [...items, item]
}

/**
 * Place an item where it happened. Messages are already in time order; a
 * plan appended after all of them showed a plan written yesterday below
 * today's conversation, as if it had just been written again.
 */
function insertByTime(items: ConversationItem[], item: ConversationItem): ConversationItem[] {
  const at = Date.parse(item.timestamp)
  if (Number.isNaN(at)) return [...items, item]
  const idx = items.findIndex(i => Date.parse(i.timestamp) > at)
  return idx < 0 ? [...items, item] : [...items.slice(0, idx), item, ...items.slice(idx)]
}

export function buildConversationItems(
  msgs: RawMessage[],
  task: Task,
): ConversationItem[] {
  let convItems: ConversationItem[] = []
  for (const m of msgs) {
    const ts = m.created_at || new Date().toISOString()
    if (m.role === 'user') {
      convItems.push({ id: `hist-user-${convItems.length}`, type: 'user_message', timestamp: ts, message: { role: 'user', content: m.content } })
    } else if (m.role === 'assistant') {
      convItems.push({ id: `hist-asst-${convItems.length}`, type: 'agent_message', timestamp: ts, message: { role: 'assistant', content: m.content } })
    } else if (m.role === 'result') {
      // EVERY turn's result is a result, in the position it was produced.
      // Only the first used to qualify; later ones were demoted to plain
      // assistant messages. A task now legitimately has several turns
      // ("how much did we sell today?" then "review the socks ads"), and
      // demoting the later ones put each answer somewhere other than
      // under the question that asked for it.
      convItems = appendResult(convItems, { id: `hist-result-${convItems.length}`, type: 'result', timestamp: ts, result: m.content })
    } else if (m.role === 'tool_use') {
      try {
        const toolInfo = JSON.parse(m.content)
        convItems.push({ id: `hist-tool-${convItems.length}`, type: 'tool_call', timestamp: ts, toolCall: toolInfo })
      } catch { /* skip malformed */ }
    } else if (m.role === 'thinking') {
      convItems.push({ id: `hist-think-${convItems.length}`, type: 'thinking', timestamp: ts, thinking: { content: m.content, isStreaming: false } })
    } else if (m.role === NOTICE_ROLE) {
      const notice = parseNotice(m.content)
      if (notice) convItems.push({ id: `hist-notice-${convItems.length}`, type: 'notice', timestamp: ts, notice })
    } else if (m.role === 'generated_image') {
      // A generated image is persisted (role='generated_image', JSON body)
      // so it re-renders inline on reload — image_generated is otherwise a
      // live-only SSE event and would vanish on navigation.
      try {
        const g = JSON.parse(m.content)
        convItems.push({ id: `hist-genimg-${convItems.length}`, type: 'generated_image', timestamp: ts, generatedImage: { requestId: `hist-${convItems.length}`, path: g.path, url: g.url, prompt: g.prompt, model: g.model, kind: g.kind } })
      } catch { /* skip malformed */ }
    }
  }
  if (task.plan_history) {
    try {
      const history = JSON.parse(task.plan_history) as { version: number; content: string; created_at: string }[]
      for (const h of history) {
        convItems = insertByTime(convItems, { id: `hist-plan-${h.version}`, type: 'plan', timestamp: h.created_at, plan: { version: h.version, content: h.content, isCurrent: h.version === history.length } })
      }
    } catch { /* fallback below */ }
  }
  if (!convItems.some(i => i.type === 'plan') && task.plan) {
    convItems.push({ id: `hist-plan-1`, type: 'plan', timestamp: new Date().toISOString(), plan: { version: 1, content: task.plan, isCurrent: true } })
  }
  // Execution separator for tasks already in the execute phase: execution
  // starts from the current plan, so the separator follows that plan.
  const execPhaseStatuses = ['running', 'waiting', 'completed', 'failed']
  if (execPhaseStatuses.includes(task.status) && task.plan) {
    const at = convItems.findIndex(i => i.type === 'plan' && i.plan?.isCurrent)
    const sep: ConversationItem = { id: `hist-exec-sep`, type: 'execution_separator', timestamp: at < 0 ? new Date().toISOString() : convItems[at].timestamp }
    convItems = at < 0 ? [...convItems, sep] : [...convItems.slice(0, at + 1), sep, ...convItems.slice(at + 1)]
  }
  // task.result is authoritative (may be resolved from a file pointer by
  // set_task_result, which emits an SSE result but persists no message).
  // The persisted role='result' messages are short transcript snippets
  // and must not win over task.result on history rebuild.
  if (task.result) convItems = refreshLatestResult(convItems, task.result)
  return convItems
}

/**
 * Show `result` — the task's authoritative latest deliverable — in the
 * thread. Used on history rebuild and when a live task_update carries a
 * new `result` (a parent whose subtasks finished gets their combined
 * answer with no agent message), so both paths draw the same thing.
 *
 * It refreshes the LAST result item, not the first. Replacing the FIRST
 * one is what drew the newest answer above the question that asked for
 * it: on a two-turn task the turn-2 ad-audit result rendered between
 * turn 1's question and turn 2's, reading as though it answered "how
 * much did we sell today?". The earlier turn's own answer was displaced
 * to the bottom of the thread as loose prose.
 */
export function refreshLatestResult(
  items: ConversationItem[],
  result: string,
): ConversationItem[] {
  let lastIdx = -1
  for (let i = items.length - 1; i >= 0; i--) {
    if (items[i].type === 'result') { lastIdx = i; break }
  }
  if (lastIdx < 0) {
    return [...items, {
      id: 'hist-result-final',
      type: 'result' as const,
      timestamp: new Date().toISOString(),
      result,
    }]
  }
  if (items[lastIdx].result === result) return items
  const next = [...items]
  // Keep the replaced item's timestamp: it is when this result actually
  // landed — stamping "now" here would re-date the answer to whenever
  // the history happened to be rebuilt.
  next[lastIdx] = {
    id: 'hist-result-final',
    type: 'result' as const,
    timestamp: items[lastIdx].timestamp,
    result,
  }
  return next
}
