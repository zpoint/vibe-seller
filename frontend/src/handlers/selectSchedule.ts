/**
 * Handler for selecting a schedule in the scheduled-task list.
 *
 * Extracted from App.tsx so the switch-schedule click chain can be
 * unit-tested without rendering the whole tree. Holds two
 * invariants the inline version got wrong:
 *
 *   1. Clear scheduleTasks SYNCHRONOUSLY before the fetch. Without
 *      this, the previous schedule's runs stay on screen during the
 *      roundtrip — and clicking one opens that previous run's task
 *      detail even though the selected-schedule header already
 *      reflects the new schedule.
 *
 *   2. Drop responses whose schedule is no longer the in-flight
 *      one. `inFlightScheduleIdRef` holds the id of the most
 *      recently selected schedule. Covers two races:
 *        - A then B fast: A's late response fails `ref===A.id`
 *          because ref is now B.id, so A's list is dropped.
 *        - A then "clear selection" (deleteSchedule, selectStore,
 *          selectAllTasks): the caller resets the ref to null, so
 *          A's late response is likewise dropped and doesn't
 *          repopulate scheduleTasks under a null header.
 *      It is a schedule id, not a per-request token — clicking
 *      away and back to the same schedule resolves to the same
 *      data either way, so a monotonic counter adds complexity
 *      with no user-visible benefit.
 */
import type { MutableRefObject } from 'react'

export interface SelectScheduleApi {
  get<T>(url: string): Promise<T>
}

export interface SelectScheduleDeps<
  TSchedule extends { id: string },
  TTask extends { id: string },
> {
  api: SelectScheduleApi
  inFlightScheduleIdRef: MutableRefObject<string | null>
  setSelectedSchedule: (s: TSchedule) => void
  setSelectedTask: (t: null) => void
  setScheduleTasks: (tasks: TTask[]) => void
}

export async function selectSchedule<
  TSchedule extends { id: string },
  TTask extends { id: string },
>(
  schedule: TSchedule,
  deps: SelectScheduleDeps<TSchedule, TTask>,
): Promise<void> {
  deps.setSelectedSchedule(schedule)
  deps.setSelectedTask(null)
  deps.setScheduleTasks([])
  deps.inFlightScheduleIdRef.current = schedule.id
  try {
    const tasks = await deps.api.get<TTask[]>(
      `/api/schedules/${schedule.id}/tasks`,
    )
    if (deps.inFlightScheduleIdRef.current !== schedule.id) return
    deps.setScheduleTasks(tasks)
  } catch {
    if (deps.inFlightScheduleIdRef.current !== schedule.id) return
    deps.setScheduleTasks([])
  }
}
