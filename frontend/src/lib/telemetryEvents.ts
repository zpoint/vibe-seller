// Event-name constants for sendEvent() callers. Mirror of
// app/telemetry_events.py for the frontend custom events. Hardcoded
// strings drift; one typo and a dashboard dimension goes silent.

export const FrontendEvent = {
  VIEW_CHANGED: 'view_changed',
  STORE_SWITCHED: 'store_switched',
  TASK_OPENED: 'task_opened',
  TASK_MESSAGE_SUBMITTED: 'task_message_submitted',
  PLAN_APPROVED: 'plan_approved',
  PLAN_CHANGES_REQUESTED: 'plan_changes_requested',
  SCHEDULE_CREATED: 'schedule_created',
  SCHEDULE_EDITED: 'schedule_edited',
  LANGUAGE_SWITCHED: 'language_switched',
  SETTINGS_TELEMETRY_TOGGLED: 'settings_telemetry_toggled',
} as const

export type FrontendEventName =
  (typeof FrontendEvent)[keyof typeof FrontendEvent]
