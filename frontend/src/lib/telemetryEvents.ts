// Event-name constants for sendEvent() callers. Mirror of
// app/telemetry_events.py for the frontend custom events. Hardcoded
// strings drift; one typo and a dashboard dimension goes silent.

export const FrontendEvent = {
  TASK_OPENED: 'task_opened',
  TASK_MESSAGE_SUBMITTED: 'task_message_submitted',
  PLAN_APPROVED: 'plan_approved',
  PLAN_CHANGES_REQUESTED: 'plan_changes_requested',
  SCHEDULE_EDITED: 'schedule_edited',
  SETTINGS_TELEMETRY_TOGGLED: 'settings_telemetry_toggled',
  API_REQUEST_FAILED: 'api_request_failed',
  GUIDE_SHOWN: 'guide_shown',
  GUIDE_CLOSED: 'guide_closed',
  GUIDE_LANGUAGE_SWITCHED: 'guide_language_switched',
  AI_SETUP_PROMPT_SHOWN: 'ai_setup_prompt_shown',
  AI_SETUP_PROMPT_CHOICE: 'ai_setup_prompt_choice',
  AI_SETUP_GUIDE_SHOWN: 'ai_setup_guide_shown',
  AI_SETUP_GUIDE_CLOSED: 'ai_setup_guide_closed',
  AI_SETUP_GUIDE_DOCS_OPENED: 'ai_setup_guide_docs_opened',
  AI_SETUP_COMPLETED: 'ai_setup_completed',
} as const

export type FrontendEventName =
  (typeof FrontendEvent)[keyof typeof FrontendEvent]
