// ─── Types ───────────────────────────────────────────
export type ServerPlatform = 'mac' | 'windows' | 'wsl' | 'linux'
export interface ServerInfo {
  platform: ServerPlatform
  version: string
  commit: string | null
}
export interface Store {
  id: string; name: string; browser_backend: string; browser_config: Record<string, unknown>;
  ziniao_account_id: string | null; browser_oauth: string | null;
  platforms: string[]; countries: string[];
  platform_countries: Record<string, string[]>;
  created_at: string; updated_at: string;
}
export interface Task {
  id: string; store_id: string | null; parent_task_id?: string | null; title: string; description: string | null;
  status: string; plan: string | null; plan_history: string | null; result: string | null; todos: string | null;
  wait_condition: string | null;
  error: string | null; error_category: string | null;
  plan_mode: boolean; is_plan_only?: boolean; ai_profile_id: string | null;
  schedule_id: string | null;
  batch_id: string | null;
  created_by_name: string | null;
  created_at: string; started_at: string | null; completed_at: string | null;
}
export interface TaskStep {
  id: string; task_id: string; step_index: number; name: string;
  action_type: string; status: string; screenshot_id: string | null; error: string | null;
}
export interface PendingFile {
  id: string;
  file: File;
  preview: string;
  name: string;
}
export interface WsFile {
  path: string;
  name: string;
  size: number;
  has_content?: boolean;
}
export interface WsStoreProfile {
  slug: string;
  path: string;
  files: WsFile[];
  file_count: number;
  has_content: boolean;
  data_path: string;
  data_files: WsFile[];
  data_file_count: number;
}
export interface WsSkill {
  slug: string;
  path: string;
  files: WsFile[];
  file_count: number;
  description: string;
  source: 'builtin' | 'imported' | 'custom';
  origin_url?: string;
}
export interface WsStructured {
  skills: WsSkill[];
  store_profiles: WsStoreProfile[];
  project_knowledge: WsFile[];
  local_knowledge: WsFile[];
  root_files: WsFile[];
}
export interface AgentMessage {
  role: string;
  content: string;
}
export interface TodoItem {
  content: string;
  status: 'pending' | 'in_progress' | 'completed';
  activeForm: string;
}
export interface ZiniaoAccount {
  id: string; name: string; company: string; username: string;
  socket_port: number; client_path: string | null; created_at: string;
}
export interface ZiniaoBrowserProfile {
  browser_name: string;
  browser_oauth: string;
}
export interface Profile {
  id: string
  name: string
  description: string
  env: Record<string, string>
}
export interface AuthUser {
  id: string; username: string; email: string | null; role: string; is_active: boolean;
  avatar_url: string | null; plan_mode_default: boolean; debug_mode: boolean; default_profile_id: string; created_at: string;
}
export interface EventItem {
  id: string; channel_message_id: string | null; channel_type: string | null;
  store_id: string | null; title: string; description: string | null;
  event_date: string | null; deadline: string | null; platform: string | null;
  source_text: string | null; status: string; sync_backend: string | null;
  sync_id: string | null; sync_error: string | null;
  case_id: string | null; assignees: string | null; created_by: string | null;
  priority: number; created_at: string; updated_at: string;
}
export interface EventActivity {
  id: string; event_id: string; user_id: string | null; actor_type: string;
  action: string; content: string; extra_data: string | null; created_at: string;
}

// ─── Conversation stream types ───────────────────────
export type ConversationItemType =
  | 'plan' | 'user_message' | 'agent_message'
  | 'streaming' | 'question' | 'execution_separator'
  | 'result' | 'tool_call' | 'thinking'

export interface PlanVersion {
  version: number
  content: string
  isCurrent: boolean
}

export interface ConversationItem {
  id: string
  type: ConversationItemType
  timestamp: string
  plan?: PlanVersion
  message?: { role: string; content: string }
  questions?: { request_id: string; questions: { header?: string; question: string; options?: { label: string; description?: string }[] }[] }
  result?: string
  toolCall?: { tool: string; input?: Record<string, unknown> }
  thinking?: { content: string; isStreaming: boolean }
}

export type AppView = 'tasks' | 'workspace' | 'settings'

export type SchedulePhaseMode = 'fanout' | 'single' | 'two_phase'

export type SchedulePlanStatus = 'none' | 'planning' | 'ready' | 'stale' | 'failed'

export interface Schedule {
  id: string
  store_id: string | null
  title: string
  description: string | null
  platform: string | null
  country: string | null
  plan: string | null
  schedule_type: 'minutes' | 'hours' | 'days' | 'weekly' | 'monthly'
  schedule_time: string
  schedule_day: number | null
  interval_value: number
  timezone: string
  is_active: boolean
  phase_mode: SchedulePhaseMode
  plan_mode: boolean
  // Parent finalize/reduce step registered by the plan agent for an
  // all-stores fanout schedule (null = none). See finalize_reaper.
  finalize_description: string | null
  ai_profile_id: string | null
  created_by: string
  created_at: string
  updated_at: string
  next_run: string | null
  child_task_count: number
  last_run_status: string | null
  is_system?: boolean
  plan_status: SchedulePlanStatus
  plan_version: number
  plan_error: string | null
  current_planning_task_id: string | null
  pending_questions_count: number
}

export interface EmailAccount {
  id: string
  email: string
  imap_host: string
  imap_port: number
  use_ssl: boolean
  smtp_host: string | null
  smtp_port: number | null
  smtp_use_tls: boolean
  created_at: string
  updated_at: string
}

export interface StoreEmailLink {
  id: string
  store_id: string
  email_account_id: string
  email: string
  watermark_date: string | null
  last_polled_at: string | null
}
