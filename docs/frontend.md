# Frontend

React 19 + TypeScript + Vite 7 + Tailwind CSS 4 SPA with bilingual (EN/ZH) support via react-i18next.

## Component Architecture

The frontend follows a component-based architecture with clear separation of concerns:

```
src/
  types.ts                    # All shared interfaces + AppView type
  api.ts                      # Fetch wrapper (get/post/put/patch/del)
  App.tsx                     # State management + layout shell (~280 lines)
  components/
    ProfileModal.tsx          # AI profile CRUD modal
    LoginPage.tsx             # Auth login form
    Sidebar.tsx               # Left sidebar: nav tabs, store list, workspace tree
    CreateTaskModal.tsx       # Task creation modal with file attachments
    CreateEventModal.tsx      # Event creation modal
    CreateScheduleModal.tsx   # Schedule creation modal; wraps ScheduleForm, prefills timezone from /api/settings
    EditScheduleModal.tsx     # Schedule edit modal; wraps ScheduleForm, PUTs /api/schedules/:id
    ScheduleForm.tsx          # Shared controlled form for create/edit (title, cadence, hour/minute selects, timezone)
    TimezoneSelect.tsx        # Native <select> backed by Intl.supportedValuesOf('timeZone') (no hardcoded list)
    QuestionBanner.tsx        # Agent question/answer banner
    ui/
      StatusBadge.tsx         # Task status pill
      EventStatusBadge.tsx    # Event status pill
      StepIcon.tsx            # Step status icon
      CollapsibleSection.tsx  # Collapsible markdown section
      LanguageSwitcher.tsx    # EN/ZH toggle button
      WsFileItem.tsx          # Workspace file tree item
      index.ts                # Barrel re-export
    conversation/
      ConversationStream.tsx  # Scrollable conversation timeline with render-time tool call grouping
      PlanCard.tsx            # Plan card with confirm/request-changes buttons, compact superseded style
      MessageBubble.tsx       # Single agent or user message bubble
      ExecutionSeparator.tsx  # Visual divider between plan and execution phases
      ToolCallCard.tsx        # Compact tool call line + ToolCallGroup (grouped consecutive tool calls)
      ThinkingBlock.tsx       # Streaming/collapsed thinking display + WorkingIndicator fallback
  views/
    TasksView.tsx             # Task list + task detail + agent session
    EventsView.tsx            # Event list + detail + activities
    WorkspaceView.tsx         # File editor + history viewer
    WorkspaceAssistantView.tsx  # Workspace AI chat interface
    SettingsView.tsx          # Stores, general, AI, email, account, integrations tabs (delegates to components/settings/*Panel.tsx)
  hooks/
    useSSE.ts                 # SSE event listener hook
    useUpdateFlow.ts          # the in-app update UI state (poll + modal + restart)
```

### Layout (three panels)

```
┌──────────┬──────────────┬─────────────────────────┐
│  Sidebar │  Task List   │   Task Detail           │
│  (nav +  │  + Chat      │   Conversation View     │
│  stores) │  (320px)     │   (flex-1)              │
│  (264px) │              │                         │
└──────────┴──────────────┴─────────────────────────┘
```

1. **Left sidebar** (`Sidebar.tsx`): Nav tabs (Tasks, Events, Workspace, Settings), store list, workspace file tree
2. **Middle panel**: Task/event list for selected context + chat input
3. **Right panel**: Conversation-first task detail — a unified scrollable timeline showing thinking blocks, grouped tool calls, the plan (via `PlanCard`), an `ExecutionSeparator`, then all agent/user messages (via `MessageBubble`), with a chat input at the bottom. Header shows step progress bar (`Step 2/5 ●●○○○`) clickable to scroll to plan. Both the header counter and the PlanCard step list use `TodoWrite` items as the single source of truth for step progress.

## Views

- **TasksView**: Sub-tabs (One-time / Scheduled / Patrol), task list, conversation-first task detail rendered via `ConversationStream`
- **EventsView**: Event list with status filters, event detail with activity timeline
- **WorkspaceView**: File editor for knowledge/skills files, file history viewer
- **WorkspaceAssistantView**: AI chat for workspace organization (default when entering Workspace)
- **SettingsView**: Tabbed settings — Stores, General (cross-cutting defaults: default execution mode [Auto/Review segmented control], task retention, max concurrency, default schedule timezone via `TimezoneSelect`, fanout/single schedule default, telemetry opt-out), AI (backend status + AI profiles), Email (email accounts), Account (auth toggle, profile, password, users), Integrations (API keys first — `ApiTokensPanel.tsx`, personal API keys for external agents: list, mint with a name and expiry [never by default], revoke behind a confirm; the plaintext is shown once, in `ApiTokenCreatedModal.tsx`, next to copy-paste configs for Claude Code / Codex / WorkBuddy / Qoder / Hermes Agent / Pi built by `lib/apiTokenSnippets.ts` — then Google Workspace toggle with prereq check, chat bots (WeCom/DingTalk/Feishu/Slack, `ChatBotSection.tsx` — live platform badge from `/api/chat-bots/platforms`, signing-secret field only for DingTalk/Feishu; the edit form holds the server's MASKS for the webhook URL + secret — untouched means keep, typing a new value replaces — and admins get Show/Copy buttons via `SecretReveal.tsx` → `POST /api/secrets/reveal`), Dida365/TickTick). Masked-secret handling is app-wide: the profile modal (`ProfileModal.tsx`) shows masked env keys, sends `profile_id` with the validate probe so untouched keys probe the stored secret, and offers admin reveal; the vision panel, email-account rows and the Ziniao account form (Sidebar) carry the same admin Show/Copy. Each tab's body is a separate `components/settings/*Panel.tsx` component. The `SettingsTab` union type is exported from `SettingsView.tsx` and reused by `App.tsx`; each tab has a URL slug in `lib/route.ts` (`ai` ↔ `aiAgent`); the retired `api-tokens` slug redirects to Integrations, where the API-key card lives.

## In-app update UI

`hooks/useUpdateFlow.ts` is the single owner of the update UI for one
login session: it fetches `/api/system/update-check` once (target,
merged notes, `in_app_update`), polls `/api/system/update-status`
(2 s while `downloading`/`restarting`, 10 s otherwise), and *derives*
modal visibility — no timers setting state in effects: a `ready`
update auto-opens the modal once per browser session (`稍后` / `知道了`
hide it via `sessionStorage`, keyed by version; a new browser session
pops it again), and nothing auto-opens while downloading.

`components/UpdateChip.tsx` is the sidebar footer entry, visible
whenever `supported` and the state is downloading / ready /
restarting: Windows shows `正在下载 v{x} · {pct}%` with a byte bar
(from `downloaded_bytes`/`total_bytes`), macOS/Linux an indeterminate
bar, ready flips to the accent style, restarting stays busy. Clicking
it opens `UpdateAvailableModal` regardless of dismissal.

`UpdateAvailableModal` renders per state — downloading (progress),
ready (platform/role subtitle, the Windows "需要在运行 Vibe Seller 的
电脑上操作。" note, the running-tasks warning and `仍然重启并更新`
when `running_tasks > 0`, `知道了` for members), and restarting
(spinner; the page polls `/api/version` every 2 s and `location.reload()`
once the version changed, tolerating the server being down; after
10 min on Windows / 3 min on macOS/Linux it shows the manual
instructions). An install that cannot update itself keeps today's
popup content — copy-able `vibe-seller upgrade` or the installer
download link. Every mode shows `ReleaseNotesSections`: the server's
`highlights` / `notes` (merged across versions, newest first), 亮点
collapsed after 6 and 升级须知 after 4 with `展开其余 N 条`, each item
a markdown bullet with a version tag, language picked from the UI
locale with fallback to the other, plus the GitHub changelog link.
All strings are i18n'd (`update.*` keys, en + zh).

## Routing (URL is the source of truth)

TanStack Router (`src/router.tsx`); `App` is the root component and every
leaf route renders null — what is on screen is derived from the pathname
by `src/lib/route.ts` `parseNav()`. Routes: `/` (landing), `/tasks`,
`/tasks/$taskId`, `/stores/$storeId`, `/stores/$storeId/tasks/$taskId`,
`/schedules`, `/schedules/$scheduleId`, `/stores/$storeId/schedules`,
`/stores/$storeId/schedules/$scheduleId`, `/workspace`, `/settings/$tab`
(`/settings` redirects to `/settings/stores`).

- **Landing**: `/` does not redirect synchronously. When the store list
  arrives, `App` replaces `/` with the **first store** — or with
  `/tasks` when the user has none. The jump fires only if the LIVE URL
  is still exactly `/` at that moment, so a click or deep link made
  meanwhile always wins (`src/lib/landing.ts` explains the old race the
  guard prevents). An expired session and an explicit sign-out both
  bounce to `/`, so the next login follows the same rule — logout is not
  a chosen destination.
- **Scope × sub-tab**: the store part and the Tasks/Scheduled part of
  the path are independent. Sub-tab flips stay inside the current
  scope (`/stores/$id` ↔ `/stores/$id/schedules`), sidebar clicks keep
  the sub-tab, and a flip inside one store does not reload its task
  list (`lib/route.ts` `listTarget`/`scheduleTarget` build the URLs).

## Auth expiry handling

`api.ts` exports `AUTH_EXPIRED_EVENT` (`'auth:expired'`). Every wrapper method (`get` / `post` / `put` / `patch` / `del`) inspects the response: a 401 dispatches the event on `window` and throws `Error('unauthorized')`. `App.tsx` listens for the event once on mount, clears `currentUser` + the selected task/schedule and navigates to `/`, which causes `LoginPage` to render — and re-login then resolves the landing like a fresh open (first store, or `/tasks` with none).

This makes "any button on any page redirects to login when the JWT cookie has expired" a property of the shared `api` client, not of each caller. New views/handlers automatically inherit the behavior — they only need to use `api.*` instead of raw `fetch`. Raw `fetch` calls (e.g., the `/api/auth/status` probe at startup, `handleLogin`) deliberately bypass the redirect path because they execute *before* the user is logged in.

## Responsiveness

Two rules keep the UI from feeling (or being) stuck:

- **The composer draft is not app state.** It lives in
  `lib/draftStore.ts`, a small external store read with
  `useSyncExternalStore`: a keystroke re-renders the composer and
  nothing else (as `App.tsx` state it re-rendered the sidebar, task list
  and the whole conversation). The shell still owns the draft's
  lifecycle — cleared when a task opens or is created, read on send —
  and a failed send is stashed per task (`UnsentMessage`) so its
  composer gets the text back with the error.
- **Every request has a deadline** (`lib/requestDeadline.ts`, wired into
  `api.ts`): 30 s by default, 5 minutes where a caller passes
  `timeoutMs` (git sync, browser launch, integration install). A raw
  `fetch` has none — against a server that accepted the connection and
  never answers, a send box never clears its "sending" guard — so the
  deadline rejects with `RequestTimeoutError` and callers show an error
  instead of hanging. The timeout's message carries no URL (routes hold
  store names), for exception telemetry.

## First-run guide

`components/OnboardingGuide.tsx` + `hooks/useOnboardingGuide.ts` — a
three-step guide (全部店铺 / 店铺 / 常规·定时, step 3 as two labeled
blocks) that explains which tasks can enter a store's backend. It is
owned by `Sidebar` (which already has `currentUser` and stays mounted
on mobile), not `App.tsx`, and portals to `document.body` so the
drawer's CSS transform can't trap the fixed overlay. The card is
centered in every viewport while the page dims around the step's
anchor via four rects, so the highlight stays bright under the card
(re-measured every frame while the store list settles; no anchor
visible — mobile — means dim only). The card header carries a
`中文 | English` switch using the same save path as the sidebar
button; it never marks the guide seen and never advances the step.

**Seen-state is one `localStorage` key per browser**
(`vibe-seller-guide-seen`), written only when the user clicks Done /
Skip / × — a refresh before any of those shows it again, and the
auto-show needs a signed-in user (never on the login page). Replay
via the "?" button never writes or clears the key. Accepted
trade-offs: a private window, another browser or cleared site data
shows the guide again; a shared browser shows it only to the first
person. Every storage access is try/catch-guarded: if storage throws,
the guide still shows once per page load (in-memory guard, no loop).
E2E: `authenticated_page` pre-writes the key (the overlay would
otherwise cover every UI test in a fresh browser context);
`test_onboarding_guide.py` pins the whole key story across two
contexts.

## Internationalization

Translations stored in `src/i18n/locales/`:
- `en/translation.json` — English
- `zh/translation.json` — Chinese

Usage: `const { t } = useTranslation(); t('key')` or `t('key', { count: 5 })`

**Initial language** (`i18n/index.ts`, pure `detectLanguage()`): a saved
choice (`localStorage['vibe-seller-language']`, per origin) wins; else
ANY entry in `navigator.languages` starting with `zh` → Chinese; else a
mainland-China OS time zone (`Asia/Shanghai` & friends, `PRC`) →
Chinese; else English. The time-zone branch exists because
`navigator.languages` reports the browser's own language list, not the
OS display language — a Chinese Windows running an English browser
still carries its OS time zone, and this product's users are mainly
China-based sellers. Anyone who prefers English switches once (sidebar
button, or the switch inside the first-run guide) and the choice is
saved.

**Provider-preset strings come from the server, already translated.**
Everything about a preset comes from the catalog (`app/ai/provider_presets.py`,
synced from `main`): order, values, links, and the translations. That means a
catalog update changes what users see without a frontend release. Each preset
carries `i18n: {zh: {name, group, variant}}` and each model `i18n: {zh: {label}}`.
`components/presetI18n.ts` only picks the current language's text and falls
back to the English fields. `group` stays the stable grouping key; its display
text goes in `groupLabel`. The frontend keeps no preset translation table, and
`presetI18n.test.ts` pins that.

## SSE Integration

`useSSE` hook in `hooks/useSSE.ts` connects to `/api/sse` and dispatches events:

| Event | Handler |
|-------|---------|
| `task_created` | Prepend new task to list when its `store_id` matches the active view (or null + All-stores). Deduped by id so the originating tab — which already inserts the task from the POST response — doesn't double-add. |
| `task_update` | Update task status in list |
| `task_message` | Route by role: `delta` → streaming bubble, `assistant` → agent message, `tool_use` → tool call card, `thinking`/`thinking_delta` → thinking block, `result` → result card. **Also reconciles status**: a task streaming output can't be `queued`/`pending`, so any of these promotes it to `running` (or `designing` when `plan_mode`) — defensive against a missed/clobbered `task_update(running)` (the `pending→queued→running` race); never disturbs terminal/active states. |
| `task_todos` | Update todo progress bar |
| `task_usage` | Patch the usage fields (list, schedule list, open task) — drives the footer's `TaskUsageMeter` (context bar + cost) |
| `task_questions` | Show question banner |
| `agent_done` | Mark agent session complete |
| `schedule_triggered` | Refresh schedule list |
| `ws_assistant_message` | Append workspace assistant message |
| `ws_assistant_turn_done` | TURN end — the assistant process lives across turns, so this is what clears `wsAssistantRunning` (composer back to Send, thinking indicator gone) and refreshes the workspace tree after the assistant's edits |
| `ws_assistant_done` | PROCESS exit (stop/crash/EOF) — clears `wsAssistantRunning` too, as the backstop so the flag has one clear rule |
| `event_created` / `event_updated` | Refresh event list |

## Development

```bash
cd frontend
pnpm install
pnpm dev --port 5174    # Dev server with API proxy to :8000
pnpm build              # Production build to dist/
pnpm lint               # ESLint check
```

## Vite Config

- `@vitejs/plugin-react` for JSX/React support
- `@tailwindcss/vite` for Tailwind CSS 4 integration
- Proxy: `/api` → `http://127.0.0.1:8000` (backend)
