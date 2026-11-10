/**
 * Task actions in flight on two tasks at once keep separate state.
 *
 * Observed (review, verified reachable): useTaskAction tracked one
 * in-flight task and one state slot. Stop task A against a slow server,
 * open task B and act there, come back to A while A's stop is still in
 * flight: A's button showed idle and accepted a second Stop, and A's
 * eventual error was dropped because the slot belonged to B.
 */
import { describe, it, expect, vi } from 'vitest'
import { act, renderHook } from '@testing-library/react'
import { useTaskAction } from '../hooks/useTaskAction'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string, o?: { error?: string }) => (o?.error ? `${k}: ${o.error}` : k) }),
}))

function deferred() {
  let reject!: (e: unknown) => void
  let resolve!: () => void
  const promise = new Promise<void>((res, rej) => { resolve = res; reject = rej })
  return { promise, resolve, reject }
}

describe('useTaskAction across tasks', () => {
  it('keeps each task its own pending flag, guard and error', async () => {
    const { result, rerender } = renderHook(({ id }) => useTaskAction(id), { initialProps: { id: 'task-a' } })
    const stopA = deferred()
    const fnA = vi.fn(() => stopA.promise)
    act(() => { void result.current.run('stop', fnA) })
    expect(result.current.pending).toBe('stop')

    // Open B and act there while A's stop is still in flight.
    rerender({ id: 'task-b' })
    expect(result.current.pending).toBeNull()
    const contB = deferred()
    act(() => { void result.current.run('continue', () => contB.promise) })
    expect(result.current.pending).toBe('continue')

    // Back on A: still pending, and a second Stop is ignored.
    rerender({ id: 'task-a' })
    expect(result.current.pending).toBe('stop')
    act(() => { void result.current.run('stop', fnA) })
    expect(fnA).toHaveBeenCalledTimes(1)

    // B finishes, then A fails: A's error is kept and shown on A.
    await act(async () => { contB.resolve(); await contB.promise })
    await act(async () => { stopA.reject(new Error('stop refused')); await stopA.promise.catch(() => {}) })
    expect(result.current.pending).toBeNull()
    expect(result.current.error).toMatch(/stop refused/)
    rerender({ id: 'task-b' })
    expect(result.current.error).toBeNull()
  })
})
