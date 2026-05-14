export type TaskStatus =
  | 'pending'
  | 'queued'
  | 'designing'
  | 'planned'
  | 'running'
  | 'waiting'
  | 'completed'
  | 'failed'

interface TaskUIConfig {
  isExecutePhase: boolean
  isActive: boolean // pulse indicator, "Designing..."/"Running..."
  canStopHeader: boolean // header stop button
  canSendMessage: boolean // unified send bar enabled
  canRetry: boolean
  showResult: boolean
  showModeToggle: boolean
  showPlanConfirm: boolean // also requires plan_mode=true
  canWake: boolean // wake a waiting task
}

export const TASK_UI: Record<TaskStatus, TaskUIConfig> = {
  pending: {
    isExecutePhase: false,
    isActive: false,
    canStopHeader: false,
    canSendMessage: false,
    canRetry: false,
    showResult: false,
    showModeToggle: true,
    showPlanConfirm: false,
    canWake: false,
  },
  queued: {
    isExecutePhase: false,
    isActive: false,
    canStopHeader: false,
    canSendMessage: false,
    canRetry: false,
    showResult: false,
    showModeToggle: true,
    showPlanConfirm: false,
    canWake: false,
  },
  designing: {
    isExecutePhase: false,
    isActive: true,
    canStopHeader: true,
    canSendMessage: true,
    canRetry: false,
    showResult: false,
    showModeToggle: true,
    showPlanConfirm: false,
    canWake: false,
  },
  planned: {
    isExecutePhase: false,
    isActive: false,
    canStopHeader: true,
    canSendMessage: true,
    canRetry: false,
    showResult: false,
    showModeToggle: true,
    showPlanConfirm: true,
    canWake: false,
  },
  running: {
    isExecutePhase: true,
    isActive: true,
    canStopHeader: true,
    canSendMessage: true,
    canRetry: false,
    showResult: false,
    showModeToggle: true,
    showPlanConfirm: false,
    canWake: false,
  },
  waiting: {
    isExecutePhase: true,
    isActive: false,
    canStopHeader: true,
    canSendMessage: true,
    canRetry: false,
    showResult: true,
    showModeToggle: true,
    showPlanConfirm: false,
    canWake: true,
  },
  completed: {
    isExecutePhase: true,
    isActive: false,
    canStopHeader: false,
    canSendMessage: true,
    canRetry: true,
    showResult: true,
    showModeToggle: true,
    showPlanConfirm: false,
    canWake: false,
  },
  failed: {
    isExecutePhase: true,
    isActive: false,
    canStopHeader: false,
    canSendMessage: true,
    canRetry: true,
    showResult: false,
    showModeToggle: true,
    showPlanConfirm: false,
    canWake: false,
  },
}

export function getUI(status: string): TaskUIConfig {
  return TASK_UI[status as TaskStatus] ?? TASK_UI.pending
}

/**
 * Statuses that are "progressing" — a task in any of these means
 * the schedule is still in-flight and the "Run Now" trigger must
 * be disabled to prevent overlapping runs.
 *
 * Mirrors the backend concept in `app/task_states.py`: `completed`,
 * `failed`, and `waiting` are all done-enough to allow a fresh run.
 * `waiting` specifically means the agent set a wait-condition and
 * is paused — a manual Trigger creates a NEW task, so the existing
 * waiting one isn't blocked.
 */
export const PROGRESSING_STATUSES: ReadonlySet<string> = new Set([
  'pending',
  'queued',
  'designing',
  'planned',
  'running',
])

export function isProgressing(status: string): boolean {
  return PROGRESSING_STATUSES.has(status)
}

export function hasProgressingTask(
  tasks: ReadonlyArray<{ status: string }>,
): boolean {
  return tasks.some(t => isProgressing(t.status))
}
