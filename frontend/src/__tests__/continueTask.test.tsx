/**
 * continueTask handler: the Continue button.
 *
 * Contract (contrasts with retryTask, which wipes context):
 *  - POSTs /api/tasks/{id}/messages with the canned continue message
 *    (from i18n `tasks.continueMessage`, the same resume path the chat
 *    send-box uses) — NOT /retry.
 *  - The optimistic patch advances status only; it must NOT clear
 *    error / plan / plan_history, so the task keeps its context while
 *    the resumed run streams in.
 */
import { describe, it, expect, vi } from 'vitest'
import { continueTask, continueMessage } from '../handlers/continueTask'
import type { Task } from '../types'

function makeTask(extra: Partial<Task> = {}): Task {
  return {
    id: 't1',
    store_id: null,
    title: 'T-1',
    description: null,
    status: 'failed',
    plan: 'PRIOR PLAN',
    result: null,
    todos: null,
    wait_condition: null,
    error: 'boom',
    plan_mode: false,
    ai_profile_id: 'profile-old',
    schedule_id: null,
    batch_id: null,
    created_at: '',
    started_at: null,
    completed_at: null,
    ...extra,
  } as Task
}

describe('continueTask (Continue — non-destructive resume)', () => {
  it('posts the canned message to /messages, not /retry', async () => {
    const api = {
      post: vi.fn().mockResolvedValue({}),
      get: vi.fn().mockRejectedValue(new Error('skip merge')),
    }
    await continueTask('t1', {
      api,
      profileId: 'p1',
      setTasks: vi.fn(),
      setSelectedTask: vi.fn(),
      setScheduleTasks: vi.fn(),
    })
    expect(api.post).toHaveBeenCalledWith('/api/tasks/t1/messages', {
      content: continueMessage(),
      profile_id: 'p1',
    })
    expect(api.post).not.toHaveBeenCalledWith(
      '/api/tasks/t1/retry',
      expect.anything(),
    )
  })

  it('optimistic patch bumps status but PRESERVES error/plan/history', async () => {
    const setSelectedTask = vi.fn()
    const api = {
      post: vi.fn().mockResolvedValue({}),
      get: vi.fn().mockRejectedValue(new Error('skip merge')),
    }
    await continueTask('t1', {
      api,
      profileId: 'p1',
      setTasks: vi.fn(),
      setSelectedTask,
      setScheduleTasks: vi.fn(),
    })
    // First call is the pre-await optimistic patch. Apply its updater
    // to a populated failed task and assert context survives.
    const updater = setSelectedTask.mock.calls[0][0] as (t: Task) => Task
    const patched = updater(makeTask())
    expect(patched.status).toBe('running')
    expect(patched.error).toBe('boom') // NOT cleared
    expect(patched.plan).toBe('PRIOR PLAN') // NOT cleared
    // ai_profile_id is untouched too (retry swaps it; continue must not)
    expect(patched.ai_profile_id).toBe('profile-old')
  })
})
