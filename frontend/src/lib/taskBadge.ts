/** The fields that decide what a PLANNED task is waiting for. */
export interface BadgeTask {
  status: string
  plan_mode?: boolean | null
  schedule_id?: string | null
}

/**
 * A plan-mode task whose plan waits for the user's review — the server's
 * `awaits_plan_review` (app/routers/tasks_attention.py), which decides
 * whether the top bar lists it. A scheduled fire sits in PLANNED only
 * while it waits for its turn: its plan was approved on the schedule.
 */
export function awaitsPlanReview(task: BadgeTask): boolean {
  return task.status === 'planned' && !!task.plan_mode && !task.schedule_id
}

/**
 * What a task's status badge says. PLANNED is two different waits, so
 * it is never shown as such: a plan waiting for review reads "pending
 * approval", exactly like a schedule plan does; a scheduled fire queued
 * in PLANNED reads "queued" — nothing for the user to approve.
 */
export function badgeStatus(task: BadgeTask): string {
  if (task.status !== 'planned') return task.status
  return awaitsPlanReview(task) ? 'pending_approval' : 'queued'
}
