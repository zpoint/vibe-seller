/**
 * Regression test — switching schedules must clear stale runs and
 * ignore out-of-order responses.
 *
 *   1. Click schedule A, wait for its tasks to land.
 *   2. Click schedule B — the runs list must clear SYNCHRONOUSLY
 *      so no row from A is clickable during the roundtrip.
 *   3. If A's response arrives AFTER B's (slow network), it must
 *      not overwrite B's list.
 *
 * Without these invariants the UI shows schedule B in the header
 * but schedule A in the list; clicking a row opens a task that
 * belongs to the previous schedule.
 */

import { describe, it, expect, vi } from 'vitest'
import { useRef } from 'react'
import { act, renderHook } from '@testing-library/react'
import { selectSchedule } from '../handlers/selectSchedule'

interface Sched { id: string }
interface Tsk { id: string; schedule_id: string }

function deferred<T>() {
  let resolve!: (v: T) => void
  let reject!: (e: unknown) => void
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej })
  return { promise, resolve, reject }
}

describe('selectSchedule — stale-list + race guard', () => {
  it('clears scheduleTasks synchronously before the fetch', () => {
    const { result } = renderHook(() =>
      useRef<string | null>(null),
    )
    const setScheduleTasks = vi.fn()
    const setSelectedSchedule = vi.fn()
    const setSelectedTask = vi.fn()
    // fetch blocks forever so we can observe the pre-fetch state
    const api = { get: () => new Promise<Tsk[]>(() => {}) }

    void selectSchedule<Sched, Tsk>(
      { id: 'B' },
      {
        api,
        inFlightScheduleIdRef: result.current,
        setSelectedSchedule,
        setSelectedTask,
        setScheduleTasks,
      },
    )

    // Before the fetch resolves, setScheduleTasks([]) has already
    // fired, and the ref is pinned to the in-flight request id.
    expect(setScheduleTasks).toHaveBeenCalledWith([])
    expect(setSelectedSchedule).toHaveBeenCalledWith({ id: 'B' })
    expect(setSelectedTask).toHaveBeenCalledWith(null)
    expect(result.current.current).toBe('B')
  })

  it('drops response when a newer schedule was selected mid-flight', async () => {
    const { result } = renderHook(() =>
      useRef<string | null>(null),
    )
    const setScheduleTasks = vi.fn()
    const setSelectedSchedule = vi.fn()
    const setSelectedTask = vi.fn()

    const pendingA = deferred<Tsk[]>()
    const pendingB = deferred<Tsk[]>()
    let seenA = false
    const api = {
      get: (url: string) => {
        if (url.includes('/schedules/A/')) { seenA = true; return pendingA.promise }
        if (url.includes('/schedules/B/')) return pendingB.promise
        throw new Error(`unexpected ${url}`)
      },
    }

    const deps = {
      api,
      inFlightScheduleIdRef: result.current,
      setSelectedSchedule,
      setSelectedTask,
      setScheduleTasks,
    }

    const pA = selectSchedule<Sched, Tsk>({ id: 'A' }, deps)
    // User clicks B before A resolves.
    const pB = selectSchedule<Sched, Tsk>({ id: 'B' }, deps)
    expect(seenA).toBe(true)
    expect(result.current.current).toBe('B')

    // B's response arrives first — applied.
    await act(async () => {
      pendingB.resolve([{ id: 't-b', schedule_id: 'B' }])
      await pB
    })
    const bCall = setScheduleTasks.mock.calls.find(
      ([arg]) => Array.isArray(arg) && arg[0]?.schedule_id === 'B',
    )
    expect(bCall).toBeDefined()

    // A's response arrives LATER — must be dropped.
    await act(async () => {
      pendingA.resolve([{ id: 't-a', schedule_id: 'A' }])
      await pA
    })
    const aCall = setScheduleTasks.mock.calls.find(
      ([arg]) => Array.isArray(arg) && arg[0]?.schedule_id === 'A',
    )
    expect(aCall).toBeUndefined()
  })

  it('ignores a failed late response too', async () => {
    const { result } = renderHook(() =>
      useRef<string | null>(null),
    )
    const setScheduleTasks = vi.fn()

    const pendingA = deferred<Tsk[]>()
    const api = {
      get: (url: string) => {
        if (url.includes('/schedules/A/')) return pendingA.promise
        // B's fetch resolves immediately.
        return Promise.resolve([{ id: 't-b', schedule_id: 'B' }] as Tsk[])
      },
    }

    const deps = {
      api,
      inFlightScheduleIdRef: result.current,
      setSelectedSchedule: vi.fn(),
      setSelectedTask: vi.fn(),
      setScheduleTasks,
    }

    const pA = selectSchedule<Sched, Tsk>({ id: 'A' }, deps)
    const pB = selectSchedule<Sched, Tsk>({ id: 'B' }, deps)
    await pB

    // Now A rejects — the catch branch must not overwrite B.
    setScheduleTasks.mockClear()
    await act(async () => {
      pendingA.reject(new Error('boom'))
      await pA
    })
    expect(setScheduleTasks).not.toHaveBeenCalled()
  })

  it('drops late response when selection was cleared (ref reset to null)', async () => {
    // Simulates deleteSchedule / selectStore / selectAllTasks —
    // those clear selectedSchedule to null and the App-level
    // useEffect resets the ref. A /tasks response still in flight
    // for the previous schedule must not repopulate the list.
    const { result } = renderHook(() =>
      useRef<string | null>(null),
    )
    const setScheduleTasks = vi.fn()

    const pendingA = deferred<Tsk[]>()
    const api = {
      get: (url: string) => {
        if (url.includes('/schedules/A/')) return pendingA.promise
        throw new Error(`unexpected ${url}`)
      },
    }
    const deps = {
      api,
      inFlightScheduleIdRef: result.current,
      setSelectedSchedule: vi.fn(),
      setSelectedTask: vi.fn(),
      setScheduleTasks,
    }

    const pA = selectSchedule<Sched, Tsk>({ id: 'A' }, deps)
    // User clears the selection — the App useEffect resets the ref.
    result.current.current = null

    setScheduleTasks.mockClear()
    await act(async () => {
      pendingA.resolve([{ id: 't-a', schedule_id: 'A' }])
      await pA
    })
    expect(setScheduleTasks).not.toHaveBeenCalled()
  })
})
