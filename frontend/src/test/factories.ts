/**
 * Test-double factories, with no rendering dependencies.
 *
 * Split out of `helpers.tsx` so a hook or reducer test can build a
 * `Task` without dragging in `ConversationStream`, i18next and
 * `@testing-library/react`.
 *
 * **Build fixtures through these, never as bare object literals.** Five
 * separate hand-rolled `makeTask`s had each drifted from the real
 * `Task` type — they were missing `plan_history`, `error_category` and
 * `created_by_name` — and nothing noticed, because test files used to
 * be excluded from typecheck. They are typechecked now, but one shared
 * factory is what makes the next added field a single-line change
 * instead of a hunt through every test that happens to construct one.
 */
import type {
  ConversationItem,
  ConversationItemType,
  PlanVersion,
  Schedule,
  Task,
} from '../types'

let _idCounter = 0

export function makeSchedule(overrides: Partial<Schedule> = {}): Schedule {
  return {
    id: `sched-${++_idCounter}`,
    store_id: null,
    title: 'Test Schedule',
    description: null,
    platform: null,
    country: null,
    plan: null,
    // 'days' + interval_value 1 IS the daily case; Schedule has no
    // 'daily' member.
    schedule_type: 'days',
    schedule_time: '09:00',
    schedule_day: null,
    interval_value: 1,
    timezone: 'UTC',
    is_active: true,
    phase_mode: 'single',
    plan_mode: false,
    finalize_description: null,
    ai_profile_id: null,
    created_by: 'user1',
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    next_run: null,
    child_task_count: 0,
    last_run_status: null,
    plan_status: 'none',
    plan_version: 0,
    plan_error: null,
    current_planning_task_id: null,
    pending_questions_count: 0,
    ...overrides,
  }
}

export function makeTask(overrides: Partial<Task> = {}): Task {
  return {
    id: `task-${++_idCounter}`,
    store_id: null,
    title: 'Test Task',
    description: null,
    status: 'pending',
    plan: null,
    plan_history: null,
    result: null,
    todos: null,
    wait_condition: null,
    error: null,
    error_category: null,
    plan_mode: false,
    ai_profile_id: null,
    schedule_id: null,
    batch_id: null,
    created_by_name: null,
    created_at: new Date().toISOString(),
    started_at: null,
    completed_at: null,
    ...overrides,
  }
}

export function makePlan(overrides: Partial<PlanVersion> = {}): PlanVersion {
  return {
    version: 1,
    content: '## Plan\n1. Step one\n2. Step two',
    isCurrent: true,
    ...overrides,
  }
}

export function makeConversationItem(
  type: ConversationItemType,
  overrides: Partial<ConversationItem> = {},
): ConversationItem {
  const base: ConversationItem = {
    id: `item-${++_idCounter}`,
    type,
    timestamp: new Date().toISOString(),
  }

  if (type === 'plan') {
    base.plan = makePlan()
  } else if (type === 'user_message') {
    base.message = { role: 'user', content: 'Hello' }
  } else if (type === 'agent_message') {
    base.message = { role: 'assistant', content: 'Working on it...' }
  } else if (type === 'streaming') {
    base.message = { role: '_streaming', content: 'typing...' }
  } else if (type === 'result') {
    base.result = 'Task completed successfully'
  } else if (type === 'question') {
    base.questions = {
      request_id: 'q1',
      questions: [
        { question: 'Which option?', options: [{ label: 'A' }, { label: 'B' }] },
      ],
    }
  } else if (type === 'tool_call') {
    base.toolCall = { tool: 'Read', input: { file_path: 'app/models.py' } }
  } else if (type === 'thinking') {
    base.thinking = { content: 'Analyzing the code...', isStreaming: false }
  }

  return { ...base, ...overrides }
}
