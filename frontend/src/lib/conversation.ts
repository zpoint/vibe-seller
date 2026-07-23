/**
 * Rebuild the conversation stream for an opened task from its persisted
 * messages + task state. Pure (no React / no fetch) so it's unit-tested
 * directly and keeps App.tsx lean. Extracted from loadTaskById.
 */
import type { ConversationItem, Task } from '../types'

export interface RawMessage {
  role: string
  content: string
  created_at?: string
}

export function buildConversationItems(
  msgs: RawMessage[],
  task: Task,
): ConversationItem[] {
  const convItems: ConversationItem[] = []
  let hasSeenResult = false
  for (const m of msgs) {
    const ts = m.created_at || new Date().toISOString()
    if (m.role === 'user') {
      convItems.push({ id: `hist-user-${convItems.length}`, type: 'user_message', timestamp: ts, message: { role: 'user', content: m.content } })
    } else if (m.role === 'assistant') {
      convItems.push({ id: `hist-asst-${convItems.length}`, type: 'agent_message', timestamp: ts, message: { role: 'assistant', content: m.content } })
    } else if (m.role === 'result') {
      if (!hasSeenResult) {
        convItems.push({ id: `hist-result-${convItems.length}`, type: 'result', timestamp: ts, result: m.content })
        hasSeenResult = true
      } else {
        convItems.push({ id: `hist-asst-${convItems.length}`, type: 'agent_message', timestamp: ts, message: { role: 'assistant', content: m.content } })
      }
    } else if (m.role === 'tool_use') {
      try {
        const toolInfo = JSON.parse(m.content)
        convItems.push({ id: `hist-tool-${convItems.length}`, type: 'tool_call', timestamp: ts, toolCall: toolInfo })
      } catch { /* skip malformed */ }
    } else if (m.role === 'thinking') {
      convItems.push({ id: `hist-think-${convItems.length}`, type: 'thinking', timestamp: ts, thinking: { content: m.content, isStreaming: false } })
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
        convItems.push({ id: `hist-plan-${h.version}`, type: 'plan', timestamp: h.created_at, plan: { version: h.version, content: h.content, isCurrent: h.version === history.length } })
      }
    } catch { /* fallback below */ }
  }
  if (!convItems.some(i => i.type === 'plan') && task.plan) {
    convItems.push({ id: `hist-plan-1`, type: 'plan', timestamp: new Date().toISOString(), plan: { version: 1, content: task.plan, isCurrent: true } })
  }
  // Execution separator for tasks already in the execute phase.
  const execPhaseStatuses = ['running', 'waiting', 'completed', 'failed']
  if (execPhaseStatuses.includes(task.status) && task.plan) {
    convItems.push({ id: `hist-exec-sep`, type: 'execution_separator', timestamp: new Date().toISOString() })
  }
  // task.result is authoritative (may be resolved from a file pointer by
  // set_task_result, which emits an SSE result but persists no message).
  // The persisted role='result' messages are short transcript snippets
  // and must not win over task.result on history rebuild.
  if (task.result) {
    const existingIdx = convItems.findIndex(i => i.type === 'result')
    const finalResult = {
      id: 'hist-result-final',
      type: 'result' as const,
      timestamp: new Date().toISOString(),
      result: task.result,
    }
    if (existingIdx >= 0) {
      const stale = convItems[existingIdx]
      convItems[existingIdx] = finalResult
      if (stale.type === 'result' && stale.result) {
        convItems.push({ id: `hist-asst-from-stale-result-${convItems.length}`, type: 'agent_message', timestamp: stale.timestamp, message: { role: 'assistant', content: stale.result } })
      }
    } else {
      convItems.push(finalResult)
    }
  }
  return convItems
}
