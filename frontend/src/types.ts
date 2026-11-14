// ─── Types ───────────────────────────────────────────
export type ServerPlatform = 'mac' | 'windows' | 'wsl' | 'linux'
export interface ServerInfo {
  platform: ServerPlatform
  version: string
  commit: string | null
}
export interface ReleaseNote {
  version: string; name: string; body: string; url: string; published_at: string | null;
}
export interface UpdateCheckResult {
  dev: boolean
  update_available?: boolean
  current_version?: string
  latest_version?: string
  channel?: 'stable' | 'nightly'
  platform?: ServerPlatform
  upgrade_command?: string | null
  download_url?: string | null
  releases_page_url?: string
  releases?: ReleaseNote[]
  /** True when this install updates in-app (background download +
   *  "重启并更新"); false → the popup shows the manual upgrade path. */
  in_app_update?: boolean
  /** Release notes of all pending versions merged by section, newest
   *  version first (app/release_notes.py). */
  highlights?: ReleaseHighlight[]
  notes?: ReleaseNoteItem[]
}
export interface ReleaseHighlight { version: string; zh: string; en: string }
export interface ReleaseNoteItem { version: string; text: string }
/** GET /api/system/release-notes?since=&until= — the merged notes for a
 *  range this server already crossed. /update-check cannot describe it:
 *  it only ever lists releases NEWER than the running version. */
export interface ReleaseNotesRange {
  since: string
  until: string
  /** Version tags, newest first — not the release objects. */
  releases: string[]
  highlights: ReleaseHighlight[]
  notes: ReleaseNoteItem[]
}
export type AutoUpdateState = 'idle' | 'downloading' | 'ready' | 'error' | 'restarting'
/** Which of the two apply paths a release is on. `pip` changes only the
 *  Python package (a few MB, applied by the server itself when idle);
 *  `installer` changes the bundled runtime, so nothing happens until an
 *  admin clicks. null = nothing is staged. */
export type UpdatePath = 'pip' | 'installer'
export type LastUpdateOutcome = 'ok' | 'rolled_back' | 'failed'
/** How the last apply was started: by the idle watcher, or by a click
 *  (the latter after the ready-but-never-idle day was up). */
export type LastUpdateTrigger = 'auto_idle' | 'manual' | 'manual_after_24h'
/** What the PREVIOUS apply did, kept by the server across restarts
 *  (`<VIBE_HOME>/updates/last.json`). Without it a rolled-back update
 *  would look like one that never happened. */
export interface LastUpdate {
  status: LastUpdateOutcome
  from: string
  to: string
  /** ISO8601 UTC. */
  at: string
  path: UpdatePath
  trigger: LastUpdateTrigger
  stage: string | null
  error: string | null
  error_class: string | null
  seconds: number | null
}
export interface UpdateStatus {
  supported: boolean
  state: AutoUpdateState
  version: string | null
  current_version: string
  downloaded_bytes: number | null
  total_bytes: number | null
  error: string | null
  running_tasks: number
  platform: ServerPlatform
  path: UpdatePath | null
  /** True exactly when the server will apply this on its own: the pip
   *  path, AND not a release it already applied and had to put back.
   *  A rolled-back release is never auto-applied a second time — an
   *  admin's click is the only thing that runs it again. */
  auto_apply: boolean
  /** The release an apply already rolled back (or failed to install),
   *  when that is the one on offer. Null for every other version: a
   *  newer release clears it and auto-apply comes back by itself. */
  refusal: { version: string; status: string; stage: string | null } | null
  /** The server's answer to "should we ask the admin?" — installer: as
   *  soon as it is ready; pip: only after a day of never being idle.
   *  The frontend reads this flag and keeps no timer of its own. */
  popup_due: boolean
  /** Epoch seconds when this version reached `ready`, or null. */
  ready_since: number | null
  last_update: LastUpdate | null
}
export interface Store {
  id: string; name: string; browser_backend: string; browser_config: Record<string, unknown>;
  ziniao_account_id: string | null; browser_oauth: string | null;
  platforms: string[]; countries: string[];
  platform_countries: Record<string, string[]>;
  created_at: string; updated_at: string;
}
export interface Task extends TaskUsage {
  id: string; store_id: string | null; parent_task_id?: string | null; title: string; description: string | null;
  status: string; plan: string | null; plan_history: string | null; result: string | null; todos: string | null;
  // Last submission, kept even when a stop gate refused it, plus that
  // refusal as a JSON string[] of unmet gaps. See app/task_outcome.py.
  submitted_result?: string | null; review_gaps?: string | null; submission_count?: number;
  wait_condition: string | null;
  error: string | null; error_category: string | null;
  plan_mode: boolean; is_plan_only?: boolean; ai_profile_id: string | null;
  schedule_id: string | null;
  batch_id: string | null;
  created_by_name: string | null;
  // `updated_at` is on every task the API returns (TaskResponse); it was
  // simply never declared here, so code sorting by last activity could
  // not be type-checked.
  created_at: string; updated_at: string; started_at: string | null; completed_at: string | null;
}
/** Live usage meter (app/task_usage.py) — on the task row and pushed
 *  during a run as the `task_usage` SSE event, with these names. */
export interface TaskUsage {
  cost_usd?: number | null
  input_tokens?: number | null
  output_tokens?: number | null
  cache_read_tokens?: number | null
  cache_write_tokens?: number | null
  context_tokens?: number | null
  context_window?: number | null
  usage_model?: string | null
  /** catalog = priced from the provider's rates · included = plan /
   *  free model · unpriced = model not in the catalog. */
  cost_basis?: 'catalog' | 'included' | 'unpriced' | null
  num_turns?: number | null
}
export const TASK_USAGE_FIELDS = [
  'cost_usd', 'input_tokens', 'output_tokens', 'cache_read_tokens',
  'cache_write_tokens', 'context_tokens', 'context_window', 'usage_model',
  'cost_basis', 'num_turns',
] as const satisfies readonly (keyof TaskUsage)[]
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
/** A chat attachment uploaded to staging (outside the agent's cwd) and
 *  waiting for Send. Carries a preview URL, never an absolute path. */
export interface StagedAttachment {
  id: string;
  filename: string;
  contentType: string;
  url: string;
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
  load_global_mcp?: boolean
}
export interface AuthUser {
  id: string; username: string; email: string | null; role: string; is_active: boolean;
  avatar_url: string | null; plan_mode_default: boolean; debug_mode: boolean; default_profile_id: string; sync_profile_to_schedules: boolean; created_at: string;
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

// One selectable image model, as served by GET /api/vision/config and
// the image_request SSE event. Price is a per-image hint in both USD
// and (fixed-rate) CNY; the UI shows one based on the active language.
export interface ImageModelOption {
  id: string
  provider: string
  label: string
  usd: number
  cny: number
  default?: boolean
}

// ─── Conversation stream types ───────────────────────
export type ConversationItemType =
  | 'plan' | 'user_message' | 'agent_message'
  | 'streaming' | 'question' | 'execution_separator'
  | 'result' | 'tool_call' | 'thinking'
  | 'image_request' | 'generated_image'

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
  // The user sent a chat follow-up instead of answering this question —
  // the card is retired as "you replied instead", not "answered".
  questionInterrupted?: boolean
  result?: string
  toolCall?: { tool: string; input?: Record<string, unknown> }
  thinking?: { content: string; isStreaming: boolean }
  imageRequest?: {
    requestId: string
    prompt: string
    model: string
    models: ImageModelOption[]
    referenceImages: string[]
    outputName?: string
    kind?: string
    resolved?: boolean
    expired?: boolean
    // The user sent a chat message instead of confirming — the card is
    // retired (non-actionable) and the image was NOT generated.
    interrupted?: boolean
    // Confirmed and the image is actively generating (kie.ai poll,
    // ~1-2 min). Drives the "generating…" state in ImageRequestCard.
    generating?: boolean
  }
  generatedImage?: {
    requestId: string
    path: string
    url: string
    prompt?: string
    model?: string
    kind?: string
  }
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
  // Fanout children run one store at a time (Schedule.fanout_serial).
  fanout_serial?: boolean
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

// ─── API tokens (external-agent access) ───────────────
// A personal access token: an external agent (Claude Code, Codex,
// WorkBuddy, …) presents it as `Authorization: Bearer vst_…` and acts
// as the user who minted it. The plaintext token is returned exactly
// once, at creation — the list only ever shows the display prefix.
export interface ApiToken {
  id: string
  name: string
  prefix: string
  created_at: string
  /** null until the token has been used at least once. */
  last_used_at: string | null
  /** null = never expires. */
  expires_at: string | null
}

export interface CreatedApiToken extends ApiToken {
  /** Plaintext token — shown once, never stored server-side. */
  token: string
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
  /** Agents may send from this mailbox. Off by default. */
  allow_send: boolean
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
