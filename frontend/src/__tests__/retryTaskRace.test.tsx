/**
 * Reproduces the race observed in production:
 *
 *   T=0  user clicks Retry on a failed task → POST /api/tasks/{id}/retry
 *   T=A  backend resets to PENDING and emits task_update(pending)
 *        — useSSE patches the list to status=pending
 *   T=B  backend's `await schedule_or_run` advances → emits
 *        task_update(queued)
 *        — useSSE patches the list to status=queued
 *   T=C  agent starts → emits task_update(running)
 *        — useSSE patches the list to status=running
 *   T=D  POST response arrives at the client.
 *   T=D+ε  retryTask handler resumes after `await api.post(...)` and
 *          (the BUG) writes `status='pending'` to the list, clobbering
 *          the SSE-driven progress to `running`.
 *
 * The fix moves the optimistic clear *before* the await and replaces
 * the post-await write with a merge-only refetch — so SSE updates
 * survive.
 *
 * We assert at the boundary by feeding a fake api stub that defers
 * its POST resolution, fire SSE-style state mutations between the
 * await start and resolution, and check that the final `tasks[]`
 * reflects `running` (not `pending`).
 */
import { describe, it, expect, vi } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { useState } from 'react'
import { retryTask } from '../handlers/retryTask'
import type { Task } from '../types'

function makeTask(
  id: string,
  status: Task['status'] = 'failed',
  extra: Partial<Task> = {},
): Task {
  return {
    id,
    store_id: null,
    title: `T-${id}`,
    description: null,
    status,
    plan: null,
    result: null,
    todos: null,
    wait_condition: null,
    error: null,
    plan_mode: false,
    ai_profile_id: 'profile-old',
    schedule_id: null,
    batch_id: null,
    created_at: '',
    started_at: null,
    completed_at: null,
    ...extra,
  }
}

function useHarness(initial: Task[]) {
  const [tasks, setTasks] = useState<Task[]>(initial)
  const [selectedTask, setSelectedTask] = useState<Task | null>(initial[0] ?? null)
  const [scheduleTasks, setScheduleTasks] = useState<Task[]>([])
  return {
    tasks,
    setTasks,
    selectedTask,
    setSelectedTask,
    scheduleTasks,
    setScheduleTasks,
  }
}

describe('retryTask race vs concurrent SSE task_update', () => {
  it('REGRESSION: SSE running update arriving during the POST must not be clobbered by the optimistic clear', async () => {
    const taskId = 'task-1'
    const { result } = renderHook(() => useHarness([makeTask(taskId, 'failed', {
      error: 'Stopped by user',
    })]))

    // Stub api.post — keep the promise pending so we control the
    // window in which "SSE events" land.
    let resolvePost: () => void = () => {}
    const postPromise = new Promise<void>(res => { resolvePost = () => res() })
    const apiStub = {
      post: vi.fn(() => postPromise),
      // Refetch returns the canonical server state (running) — the
      // backend has long-since transitioned by the time POST resolves.
      get: vi.fn(() =>
        Promise.resolve(makeTask(taskId, 'running', {
          ai_profile_id: 'profile-new',
        })),
      ),
    }

    let retryDone: Promise<void> | undefined
    act(() => {
      retryDone = retryTask(taskId, {
        api: apiStub,
        profileId: 'profile-new',
        setTasks: result.current.setTasks,
        setSelectedTask: result.current.setSelectedTask,
        setScheduleTasks: result.current.setScheduleTasks,
      })
    })

    // After step 1 (synchronous optimistic clear) the list should
    // have flipped to pending — this is the user-visible "click took
    // effect" feedback.
    expect(result.current.tasks[0].status).toBe('pending')
    expect(result.current.tasks[0].error).toBeNull()

    // Now, while POST is in flight, simulate the useSSE handler
    // applying task_update(running) — same shape as line 117 of
    // useSSE.ts: `setTasks(prev => prev.map(...))`. THIS is the
    // event the bug used to clobber.
    act(() => {
      result.current.setTasks(prev =>
        prev.map(t =>
          t.id === taskId ? { ...t, status: 'running', error: null } : t,
        ),
      )
      result.current.setSelectedTask(prev =>
        prev && prev.id === taskId
          ? { ...prev, status: 'running', error: null }
          : prev,
      )
    })
    expect(result.current.tasks[0].status).toBe('running')

    // POST resolves. retryTask should kick off the merge-refetch
    // (which returns status=running), and then complete.
    await act(async () => {
      resolvePost()
      await retryDone
      // Let the get().then() chain settle.
      await Promise.resolve()
      await Promise.resolve()
    })

    // Final state: status MUST still be 'running'. The pre-fix
    // `setTasks(prev => prev.map(... status:'pending'))` after the
    // await would have flipped it back to pending here.
    expect(result.current.tasks[0].status).toBe('running')
    expect(result.current.selectedTask?.status).toBe('running')
    // Profile id flips through the merge so the new profile sticks.
    expect(result.current.tasks[0].ai_profile_id).toBe('profile-new')
  })

  it('clears error / plan / plan_history synchronously on click', () => {
    const taskId = 'task-2'
    const { result } = renderHook(() => useHarness([makeTask(taskId, 'failed', {
      error: 'previous run failed',
      plan: '## stale plan',
      plan_history: '[{"version":1}]',
    })]))

    // Never-resolving POST — we only care about the synchronous
    // optimistic clear here.
    const apiStub = {
      post: vi.fn(() => new Promise<void>(() => {})),
      get: vi.fn(() => Promise.resolve(makeTask(taskId, 'running'))),
    }

    act(() => {
      retryTask(taskId, {
        api: apiStub,
        profileId: 'profile-x',
        setTasks: result.current.setTasks,
        setSelectedTask: result.current.setSelectedTask,
        setScheduleTasks: result.current.setScheduleTasks,
      })
    })

    expect(result.current.tasks[0].status).toBe('pending')
    expect(result.current.tasks[0].error).toBeNull()
    expect(result.current.tasks[0].plan).toBeNull()
    expect(result.current.tasks[0].plan_history).toBeNull()
    expect(result.current.tasks[0].ai_profile_id).toBe('profile-x')
  })

  it('REGRESSION: POST failure must not leave the row stuck at optimistic pending', async () => {
    // When `/api/tasks/{id}/retry` fails (network/auth/server),
    // no agent starts, so no SSE will ever land. Without the catch
    // refetch, the optimistic clear would freeze the row at
    // `status='pending'` forever. The handler must call GET in the
    // catch and merge the server's actual state.
    const taskId = 'task-3'
    const { result } = renderHook(() => useHarness([makeTask(taskId, 'failed', {
      error: 'Stopped by user',
    })]))

    const apiStub = {
      // POST always fails.
      post: vi.fn(() => Promise.reject(new Error('boom: 500'))),
      // GET returns the canonical server state (still failed).
      get: vi.fn(() => Promise.resolve(makeTask(taskId, 'failed', {
        error: 'Stopped by user',
      }))),
    }

    await act(async () => {
      await retryTask(taskId, {
        api: apiStub,
        profileId: 'profile-x',
        setTasks: result.current.setTasks,
        setSelectedTask: result.current.setSelectedTask,
        setScheduleTasks: result.current.setScheduleTasks,
      })
      // Let the catch's `.then()` settle.
      await Promise.resolve()
      await Promise.resolve()
    })

    // The optimistic clear briefly flipped to `pending`; the
    // catch-path refetch then merged the real `failed` status back.
    expect(result.current.tasks[0].status).toBe('failed')
    expect(result.current.tasks[0].error).toBe('Stopped by user')
    expect(apiStub.get).toHaveBeenCalledWith(`/api/tasks/${taskId}`)
  })

  it('skips selectedTask patch when a different task is selected', () => {
    const target = 'task-target'
    const other = 'task-other'
    const { result } = renderHook(() =>
      useHarness([makeTask(target, 'failed'), makeTask(other, 'completed')]),
    )

    // selectedTask defaults to the first task — point it at `other`.
    act(() => {
      result.current.setSelectedTask(makeTask(other, 'completed'))
    })

    const apiStub = {
      post: vi.fn(() => new Promise<void>(() => {})),
      get: vi.fn(() => Promise.resolve(makeTask(target, 'running'))),
    }

    act(() => {
      retryTask(target, {
        api: apiStub,
        profileId: 'p',
        setTasks: result.current.setTasks,
        setSelectedTask: result.current.setSelectedTask,
        setScheduleTasks: result.current.setScheduleTasks,
      })
    })

    // List entry for target was patched.
    expect(
      result.current.tasks.find(t => t.id === target)!.status,
    ).toBe('pending')
    // selectedTask is `other` and must be untouched.
    expect(result.current.selectedTask?.id).toBe(other)
    expect(result.current.selectedTask?.status).toBe('completed')
  })
})
