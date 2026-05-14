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
- **SettingsView**: Tabbed settings — Stores, General (cross-cutting defaults: default execution mode [Auto/Review segmented control], task retention, max concurrency, default schedule timezone via `TimezoneSelect`, fanout/single schedule default, telemetry opt-out), AI (backend status + AI profiles), Email (email accounts), Account (auth toggle, profile, password, users), Integrations (Google Workspace toggle with prereq check, WeCom bot, Dida365/TickTick). Each tab's body is a separate `components/settings/*Panel.tsx` component. The `SettingsTab` union type is exported from `SettingsView.tsx` and reused by `App.tsx`.

## Auth expiry handling

`api.ts` exports `AUTH_EXPIRED_EVENT` (`'auth:expired'`). Every wrapper method (`get` / `post` / `put` / `patch` / `del`) inspects the response: a 401 dispatches the event on `window` and throws `Error('unauthorized')`. `App.tsx` listens for the event once on mount and clears `currentUser` + the selected task/schedule, which causes `LoginPage` to render.

This makes "any button on any page redirects to login when the JWT cookie has expired" a property of the shared `api` client, not of each caller. New views/handlers automatically inherit the behavior — they only need to use `api.*` instead of raw `fetch`. Raw `fetch` calls (e.g., the `/api/auth/status` probe at startup, `handleLogin`) deliberately bypass the redirect path because they execute *before* the user is logged in.

## Internationalization

Translations stored in `src/i18n/locales/`:
- `en/translation.json` — English
- `zh/translation.json` — Chinese

Usage: `const { t } = useTranslation(); t('key')` or `t('key', { count: 5 })`

## SSE Integration

`useSSE` hook in `hooks/useSSE.ts` connects to `/api/sse` and dispatches events:

| Event | Handler |
|-------|---------|
| `task_created` | Prepend new task to list when its `store_id` matches the active view (or null + All-stores). Deduped by id so the originating tab — which already inserts the task from the POST response — doesn't double-add. |
| `task_update` | Update task status in list |
| `task_message` | Route by role: `delta` → streaming bubble, `assistant` → agent message, `tool_use` → tool call card, `thinking`/`thinking_delta` → thinking block, `result` → result card |
| `task_todos` | Update todo progress bar |
| `task_questions` | Show question banner |
| `agent_done` | Mark agent session complete |
| `schedule_triggered` | Refresh schedule list |
| `ws_assistant_message` | Append workspace assistant message |
| `ws_assistant_done` | Mark assistant session complete |
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
