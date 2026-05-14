/**
 * Handler for the schedule "Run Now" button click.
 *
 * Extracted from App.tsx so the full click-to-state chain can be
 * unit-tested without rendering the whole tree. Takes its
 * dependencies (api client, state setters, currently-selected
 * schedule id) as parameters — pure plumbing + one contract:
 *
 *   1. POST /api/schedules/{id}/trigger
 *   2. Refetch `/api/schedules` → setSchedules
 *   3. If the triggered schedule is the one currently selected,
 *      refetch `/api/schedules/{id}/tasks` → setScheduleTasks
 *
 * Step 3 is the one the bug was missing. Without it, the
 * scheduleTasks state stayed stale until the async
 * `schedule_triggered` SSE event arrived, leaving the button
 * enabled and the new task invisible for that window.
 *
 * The SSE handler still runs in parallel — this function just
 * makes the visible update synchronous with the user's click so
 * there's no UI lag.
 */
export interface TriggerScheduleApi {
  post(url: string): Promise<unknown>
  get<T>(url: string): Promise<T>
}

export interface TriggerScheduleDeps<
  TSchedule extends { id: string },
  TTask extends { id: string },
> {
  api: TriggerScheduleApi
  selectedScheduleId: string | undefined
  setSchedules: (schedules: TSchedule[]) => void
  setScheduleTasks: (tasks: TTask[]) => void
}

export async function triggerSchedule<
  TSchedule extends { id: string },
  TTask extends { id: string },
>(
  scheduleId: string,
  deps: TriggerScheduleDeps<TSchedule, TTask>,
): Promise<void> {
  await deps.api.post(`/api/schedules/${scheduleId}/trigger`)

  // Always refetch schedules — `next_run` timestamps may have
  // advanced after the trigger.
  try {
    const schedules = await deps.api.get<TSchedule[]>('/api/schedules')
    deps.setSchedules(schedules)
  } catch {
    /* non-fatal — SSE will retry */
  }

  // Single-mode schedules launch `_run_single_job` async, so the
  // POST returns before the child task commits. The
  // `schedule_triggered` SSE event refreshes scheduleTasks once
  // the task exists; refetching HERE makes the button greying +
  // new-task-row appearance synchronous with the click even if
  // SSE is slow or drops.
  if (deps.selectedScheduleId === scheduleId) {
    try {
      const tasks = await deps.api.get<TTask[]>(
        `/api/schedules/${scheduleId}/tasks`,
      )
      deps.setScheduleTasks(tasks)
    } catch {
      /* non-fatal — SSE will retry */
    }
  }
}
