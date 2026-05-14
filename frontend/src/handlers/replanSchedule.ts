/**
 * Handler for the schedule "Re-plan" button click.
 *
 * POSTs to /api/schedules/{id}/replan (idempotent on the server) and
 * refetches /api/schedules so the list badges update immediately.
 * If the just-replanned schedule is the currently-selected one we
 * also refetch its child tasks so the new plan-only task appears
 * without waiting for SSE.
 */
export interface ReplanScheduleApi {
  post(url: string): Promise<unknown>
  get<T>(url: string): Promise<T>
}

export interface ReplanScheduleDeps<
  TSchedule extends { id: string },
  TTask extends { id: string },
> {
  api: ReplanScheduleApi
  selectedScheduleId: string | undefined
  setSchedules: (schedules: TSchedule[]) => void
  setScheduleTasks: (tasks: TTask[]) => void
}

export async function replanSchedule<
  TSchedule extends { id: string },
  TTask extends { id: string },
>(
  scheduleId: string,
  deps: ReplanScheduleDeps<TSchedule, TTask>,
): Promise<void> {
  await deps.api.post(`/api/schedules/${scheduleId}/replan`)

  try {
    const schedules = await deps.api.get<TSchedule[]>('/api/schedules')
    deps.setSchedules(schedules)
  } catch {
    /* non-fatal — SSE will retry */
  }

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
