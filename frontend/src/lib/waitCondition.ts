/**
 * What a WAITING task is waiting for, read from its `wait_condition`.
 *
 * The backend writes two shapes: a parent waiting on its sub-tasks
 * (`{strategy: 'children'}`, ended only by the last sub-task finishing,
 * no deadline) and an agent-declared wait (`check_strategy` 'manual' or
 * 'email', with a reason and a max-wait deadline). Every view reads it
 * through here so neither shape is mislabelled as the other.
 */
export type WaitKind = 'children' | 'email' | 'manual'

export interface WaitInfo {
  kind: WaitKind
  // The parsed condition; agent-declared fields are only meaningful
  // when kind !== 'children'.
  cond: Record<string, unknown>
}

export function parseWaitCondition(raw: string | null | undefined): WaitInfo | null {
  if (!raw) return null
  let cond: unknown
  try {
    cond = JSON.parse(raw)
  } catch {
    return null
  }
  if (!cond || typeof cond !== 'object') return null
  const c = cond as Record<string, unknown>
  if (c.strategy === 'children') return { kind: 'children', cond: c }
  return { kind: c.check_strategy === 'email' ? 'email' : 'manual', cond: c }
}

/** i18n key of the short badge shown next to a waiting task. */
export function waitBadgeKey(kind: WaitKind): string {
  if (kind === 'children') return 'waiting.subtasks'
  return kind === 'email' ? 'waiting.autoEmail' : 'waiting.manual'
}

const DAY_MS = 86_400_000

/**
 * When the wait ends unmet: `waiting_since` + `max_wait_days` — the
 * server's `wait_deadline` (app/scheduler/waiting.py), the moment its
 * checker stops the task. Null for a wait with no clock.
 */
export function waitDeadline(cond: Record<string, unknown>): Date | null {
  if (typeof cond.waiting_since !== 'string') return null
  const since = Date.parse(cond.waiting_since)
  if (Number.isNaN(since)) return null
  const days = Number(cond.max_wait_days ?? 30)
  return new Date(since + (Number.isFinite(days) ? days : 30) * DAY_MS)
}

/** Whole days left until *deadline*, rounded up: "1 day left" up to
 * the last minute, 0 only once the deadline has passed. */
export function daysLeft(deadline: Date, now: number): number {
  return Math.max(0, Math.ceil((deadline.getTime() - now) / DAY_MS))
}
