import { useState, useEffect, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { ProfileModal } from './components/ProfileModal'
import { LoginPage } from './components/LoginPage'
import { Sidebar } from './components/Sidebar'
import { useWsFiles } from './hooks/useWsFiles'
import { useUpdateCheck } from './hooks/useUpdateCheck'
import { useIsMobile } from './hooks/useIsMobile'
import { CreateTaskModal } from './components/CreateTaskModal'
import { CreateScheduleModal } from './components/CreateScheduleModal'
import { UpdateAvailableModal } from './components/UpdateAvailableModal'
import { TasksView } from './views/TasksView'
import { WorkspaceView } from './views/WorkspaceView'
import { WorkspaceAssistantView } from './views/WorkspaceAssistantView'
import { SettingsView, type SettingsTab } from './views/SettingsView'
import { useSSE } from './hooks/useSSE'
import { api, AUTH_EXPIRED_EVENT } from './api'
import { sendEvent, lengthBucket } from './lib/telemetry'
import { FrontendEvent } from './lib/telemetryEvents'
import { triggerSchedule as triggerScheduleHandler } from './handlers/triggerSchedule'
import { replanSchedule as replanScheduleHandler } from './handlers/replanSchedule'
import { selectSchedule as selectScheduleHandler } from './handlers/selectSchedule'
import { submitCreateTask as submitCreateTaskHandler } from './handlers/submitCreateTask'
import { retryTask as retryTaskHandler } from './handlers/retryTask'
import { continueTask as continueTaskHandler } from './handlers/continueTask'
import type {
  Store, Task, TaskStep, AgentMessage, TodoItem, AuthUser, Profile,
  ServerPlatform,
  WsStructured, ZiniaoAccount, ZiniaoBrowserProfile,
  AppView, PendingFile, Schedule, EmailAccount, ConversationItem,
} from './types'

export default function App() {
  const { t } = useTranslation()

  // Auth state
  const [currentUser, setCurrentUser] = useState<AuthUser | null>(null)
  const [authChecked, setAuthChecked] = useState(false)
  const [serverPlatform, setServerPlatform] = useState<ServerPlatform | null>(null)
  const [serverVersion, setServerVersion] = useState<string>('')
  const [loginIdentifier, setLoginIdentifier] = useState('')
  const [loginPassword, setLoginPassword] = useState('')
  const [loginError, setLoginError] = useState('')

  const [appView, setAppView] = useState<AppView>('tasks')
  // Mobile: the sidebar collapses into a slide-in drawer toggled by a
  // hamburger; desktop keeps it as a resident column.
  const isMobile = useIsMobile()
  const [navOpen, setNavOpen] = useState(false)
  const [stores, setStores] = useState<Store[]>([])
  const [selectedStore, setSelectedStore] = useState<Store | null>(null)
  const [showAllTasks, setShowAllTasks] = useState(true)
  const [tasks, setTasks] = useState<Task[]>([])
  const [selectedTask, setSelectedTask] = useState<Task | null>(null)
  const [steps, setSteps] = useState<TaskStep[]>([])
  const [screenshots, setScreenshots] = useState<Record<string, string>>({})
  const [logs, setLogs] = useState<string[]>([])
  const [newStoreName, setNewStoreName] = useState('')
  const [newStoreBackend, setNewStoreBackend] = useState('chrome')
  const [showCreateStore, setShowCreateStore] = useState(false)

  // Ziniao account state
  const [ziniaoAccounts, setZiniaoAccounts] = useState<ZiniaoAccount[]>([])
  const [selectedZiniaoAccountId, setSelectedZiniaoAccountId] = useState('')
  const [ziniaoBrowsers, setZiniaoBrowsers] = useState<ZiniaoBrowserProfile[]>([])
  const [selectedBrowserOauth, setSelectedBrowserOauth] = useState('')
  const [fetchingBrowsers, setFetchingBrowsers] = useState(false)
  const [browserFetchError, setBrowserFetchError] = useState('')
  const [ziniaoRetried, setZiniaoRetried] = useState(false)
  const [showAddAccount, setShowAddAccount] = useState(false)
  const [showAccountPassword, setShowAccountPassword] = useState(false)
  const [editingAccountId, setEditingAccountId] = useState('')
  const [newAccount, setNewAccount] = useState({ name: '', company: '', username: '', password: '' })
  const [showCreateTask, setShowCreateTask] = useState(false)

  // Agent state
  const [agentMessages, setAgentMessages] = useState<AgentMessage[]>([])
  const [todoItems, setTodoItems] = useState<TodoItem[]>([])
  const [pendingQuestions, setPendingQuestions] = useState<{ request_id: string; questions: { header?: string; question: string; options?: { label: string; description?: string }[] }[] } | null>(null)
  const [selectedAnswers, setSelectedAnswers] = useState<Record<string, string>>({})
  const [otherInputs, setOtherInputs] = useState<Record<string, string>>({})
  const [showOtherInput, setShowOtherInput] = useState<Record<string, boolean>>({})
  const [chatInput, setChatInput] = useState('')
  const [conversationItems, setConversationItems] = useState<ConversationItem[]>([])
  const questionBannerRef = useRef<HTMLDivElement>(null)
  const [debugMode, setDebugMode] = useState(false)

  // Profile state
  const [profiles, setProfiles] = useState<Profile[]>([])
  const [selectedProfileId, setSelectedProfileId] = useState<string>('default')
  const [showProfileModal, setShowProfileModal] = useState(false)
  const [editingProfile, setEditingProfile] = useState<Profile | undefined>(undefined)

  // Workspace assistant state
  const [wsAssistantActive, setWsAssistantActive] = useState(false)
  const [wsAssistantMessages, setWsAssistantMessages] = useState<AgentMessage[]>([])
  const [wsAssistantRunning, setWsAssistantRunning] = useState(false)

  // Workspace state
  const [wsStructured, setWsStructured] = useState<WsStructured | null>(null)
  const [wsSelectedFile, setWsSelectedFile] = useState<string | null>(null)
  const [wsFileContent, setWsFileContent] = useState('')
  const [wsEditorContent, setWsEditorContent] = useState('')
  const [wsEditorDirty, setWsEditorDirty] = useState(false)
  const [wsSaving, setWsSaving] = useState(false)
  const [wsExpandedStores, setWsExpandedStores] = useState<Set<string>>(new Set())
  const [wsExpandedSkills, setWsExpandedSkills] = useState<Set<string>>(new Set())
  const [wsNewFileName, setWsNewFileName] = useState('')
  const [wsNewFileSection, setWsNewFileSection] = useState<string | null>(null)
  const [wsSyncMeta, setWsSyncMeta] = useState<Record<string, unknown> | null>(null)
  const [wsSyncing, setWsSyncing] = useState(false)
  const [wsSkillsSyncing, setWsSkillsSyncing] = useState(false)
  const [wsFileHistory, setWsFileHistory] = useState<Array<{sha: string, message: string, date: string, author: string}>>([])
  const [wsShowHistory, setWsShowHistory] = useState(false)
  const [wsPreviewCommit, setWsPreviewCommit] = useState<string | null>(null)
  const [wsPreviewContent, setWsPreviewContent] = useState('')

  // Proxy config state
  const [showProxy, setShowProxy] = useState(false)
  const [newStoreProxyServer, setNewStoreProxyServer] = useState('')
  const [newStoreProxyBypass, setNewStoreProxyBypass] = useState('')

  // Schedule state
  const [taskSubTab, setTaskSubTab] = useState<'onetime' | 'scheduled'>('onetime')
  const [schedules, setSchedules] = useState<Schedule[]>([])
  const [selectedSchedule, setSelectedSchedule] = useState<Schedule | null>(null)
  const [scheduleTasks, setScheduleTasks] = useState<Task[]>([])
  // Holds the id of the schedule whose /tasks fetch is currently
  // in flight (or the one most recently selected). selectSchedule
  // stamps it at click time; the response handler drops itself if
  // the ref has since changed. Also reset to null whenever the
  // selection is cleared (see useEffect below) so a late response
  // can't repopulate scheduleTasks under a null header.
  const inFlightScheduleIdRef = useRef<string | null>(null)
  const [showCreateSchedule, setShowCreateSchedule] = useState(false)

  // Email state
  const [emailAccounts, setEmailAccounts] = useState<EmailAccount[]>([])

  // Settings state
  const [allUsers, setAllUsers] = useState<AuthUser[]>([])
  const [showAddUser, setShowAddUser] = useState(false)
  const [newUserForm, setNewUserForm] = useState({ username: '', email: '', password: '', role: 'member' })
  const [settingsTab, setSettingsTab] = useState<SettingsTab>('stores')
  const [authRequired, setAuthRequired] = useState(false)

  // Any 401 from the shared API client fires this — we drop the
  // session client-side so the LoginPage renders immediately. One
  // listener covers every page/button instead of each caller doing
  // its own redirect.
  useEffect(() => {
    const onExpired = () => {
      setCurrentUser(null)
      setAppView('tasks')
      setSelectedTask(null)
      setSelectedSchedule(null)
    }
    window.addEventListener(AUTH_EXPIRED_EVENT, onExpired)
    return () => window.removeEventListener(AUTH_EXPIRED_EVENT, onExpired)
  }, [])

  // /api/system/info on mount: platform + version (top-left).
  useEffect(() => {
    fetch('/api/system/info', { credentials: 'include' })
      .then(r => r.ok ? r.json() : null)
      .then(info => {
        if (info?.platform) setServerPlatform(info.platform)
        const v = String(info?.version || ''), c = String(info?.commit || '')
        const pick = v && !v.includes('+') && !v.includes('dev') ? v : (c || v)
        if (pick) setServerVersion(pick)
      })
      .catch(() => {})
  }, [])

  // Auth check on mount — first check if auth is required
  useEffect(() => {
    fetch('/api/auth/status')
      .then(r => r.json())
      .then(({ auth_required }) => {
        setAuthRequired(auth_required)
        return fetch('/api/auth/me', { credentials: 'include' })
          .then(r => { if (!r.ok) throw new Error(); return r.json() })
          .then(u => { setCurrentUser(u); setAuthChecked(true); if (u.default_profile_id) setSelectedProfileId(u.default_profile_id); setDebugMode(u.debug_mode ?? false) })
          .catch(() => {
            if (auth_required) { setCurrentUser(null); setAuthChecked(true) }
            else { setCurrentUser(null); setAuthChecked(true) }
          })
      })
      .catch(() => { setCurrentUser(null); setAuthChecked(true) })
  }, [])

  const { updateCheck, dismissUpdateCheck } = useUpdateCheck(currentUser)

  // ─── Mobile back-button integration ─────────────────
  // On phones the layout is a drill-down stack (nav drawer → task list
  // → task detail). Without this, the device/browser Back button leaves
  // the site entirely. We add exactly one history entry whenever a
  // "sub-screen" is open (drawer, or a selected task/schedule) and pop
  // the top-most one on `popstate`, so Back means "up one level".
  const mobileSubOpen =
    isMobile && (navOpen || !!selectedTask || !!selectedSchedule)
  const historyPushedRef = useRef(false)
  useEffect(() => {
    if (!isMobile) return
    if (mobileSubOpen && !historyPushedRef.current) {
      historyPushedRef.current = true
      window.history.pushState({ vsSub: true }, '')
    } else if (!mobileSubOpen && historyPushedRef.current) {
      // Closed via in-app UI (the ← bar / scrim): drop our history
      // entry so the next Back leaves the app as the user expects.
      historyPushedRef.current = false
      window.history.back()
    }
  }, [isMobile, mobileSubOpen])
  useEffect(() => {
    const onPop = () => {
      if (!historyPushedRef.current) return
      historyPushedRef.current = false
      // Close only the top-most level. If a lower level is still open
      // the push effect re-adds an entry for it, so each Back peels one.
      if (navOpen) setNavOpen(false)
      else if (selectedTask) setSelectedTask(null)
      else if (selectedSchedule) setSelectedSchedule(null)
    }
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [navOpen, selectedTask, selectedSchedule])

  const debugInitialized = useRef(false)
  useEffect(() => { if (!debugInitialized.current) { debugInitialized.current = true; return } if (currentUser) api.patch('/api/auth/me/debug-mode', { debug_mode: debugMode }).catch(() => {}) }, [debugMode])
  const userActedRef = useRef(false)  // Login default-select guard: flips true on any manual selectStore/selectAllTasks. A plain state ref wouldn't distinguish "clicked All Stores" from initial state (same values).
  useEffect(() => {
    if (!currentUser) return
    userActedRef.current = false; loadZiniaoAccounts(); loadProfiles()
    loadStores().then(f => { if (userActedRef.current) return; if (f?.length) selectStore(f[0]); else selectAllTasks() })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentUser])

  // ─── Data loaders ──────────────────────────────────
  const loadStores = async (): Promise<Store[]> => { const f: Store[] = await api.get('/api/stores'); setStores(f); return f }
  const loadZiniaoAccounts = async () => { try { setZiniaoAccounts(await api.get('/api/ziniao-accounts')) } catch { /* ignore */ } }
  const loadProfiles = async () => { try { const data = await api.get('/api/profiles'); setProfiles(data.profiles || []) } catch { setProfiles([]) } }
  const loadUsers = async () => { try { setAllUsers(await api.get('/api/users')) } catch { /* ignore */ } }
  const loadSchedules = async () => {
    try { setSchedules(await api.get('/api/schedules')) } catch { setSchedules([]) }
  }
  const selectSchedule = (schedule: Schedule) =>
    selectScheduleHandler(schedule, {
      api,
      inFlightScheduleIdRef,
      setSelectedSchedule,
      setSelectedTask,
      setScheduleTasks,
    })
  // Reset the in-flight ref whenever the schedule selection is
  // cleared (deleteSchedule / selectStore / selectAllTasks all
  // call setSelectedSchedule(null)). Without this, a late /tasks
  // response still passes the `ref === schedule.id` guard and
  // repopulates scheduleTasks under a now-null header.
  useEffect(() => {
    if (selectedSchedule === null) inFlightScheduleIdRef.current = null
  }, [selectedSchedule])
  const deleteSchedule = async (id: string) => {
    await api.del(`/api/schedules/${id}`)
    setSchedules(prev => prev.filter(s => s.id !== id))
    if (selectedSchedule?.id === id) { setSelectedSchedule(null); setScheduleTasks([]) }
  }
  const toggleSchedulePause = async (id: string, isActive: boolean) => {
    const endpoint = isActive ? 'pause' : 'resume'
    const updated = await api.post(`/api/schedules/${id}/${endpoint}`)
    setSchedules(prev => prev.map(s => s.id === id ? updated : s))
    if (selectedSchedule?.id === id) setSelectedSchedule(updated)
  }
  const onScheduleUpdated = (updated: Schedule) => {
    setSchedules(prev => prev.map(s => s.id === updated.id ? updated : s))
    if (selectedSchedule?.id === updated.id) setSelectedSchedule(updated)
  }
  const triggerSchedule = (id: string) =>
    triggerScheduleHandler(id, {
      api,
      selectedScheduleId: selectedSchedule?.id,
      setSchedules,
      setScheduleTasks,
    })
  const replanSchedule = (id: string) =>
    replanScheduleHandler(id, {
      api,
      selectedScheduleId: selectedSchedule?.id,
      setSchedules,
      setScheduleTasks,
    })
  const loadEmailAccounts = async () => {
    try { setEmailAccounts(await api.get('/api/email-accounts')) } catch { setEmailAccounts([]) }
  }
  const loadWsStructured = async () => { setWsStructured(await api.get('/api/workspace/structured')) }
  const loadSyncMeta = async () => { try { setWsSyncMeta(await api.get('/api/workspace/knowledge/sync-meta')) } catch { /* ignore */ } }

  // ─── Ziniao helpers ────────────────────────────────
  const createZiniaoAccount = async () => {
    if (!newAccount.company.trim() || !newAccount.username.trim() || !newAccount.password.trim()) return
    await api.post('/api/ziniao-accounts', { ...newAccount, name: newAccount.name.trim() || newAccount.company.trim() })
    setNewAccount({ name: '', company: '', username: '', password: '' }); setShowAddAccount(false); await loadZiniaoAccounts()
  }
  const updateZiniaoAccount = async () => {
    if (!editingAccountId || !newAccount.company.trim() || !newAccount.username.trim()) return
    const body: Record<string, string> = { company: newAccount.company, username: newAccount.username, name: newAccount.name.trim() || newAccount.company.trim() }
    if (newAccount.password.trim()) body.password = newAccount.password
    await api.put(`/api/ziniao-accounts/${editingAccountId}`, body)
    setNewAccount({ name: '', company: '', username: '', password: '' }); setEditingAccountId(''); setShowAddAccount(false); setBrowserFetchError('')
    await loadZiniaoAccounts()
    if (selectedZiniaoAccountId === editingAccountId) fetchBrowserProfiles(editingAccountId)
  }
  const deleteZiniaoAccount = async (accountId: string) => {
    await api.del(`/api/ziniao-accounts/${accountId}`)
    if (selectedZiniaoAccountId === accountId) { setSelectedZiniaoAccountId(''); setZiniaoBrowsers([]); setBrowserFetchError('') }
    await loadZiniaoAccounts()
  }
  const fetchBrowserProfiles = async (accountId: string) => {
    if (!accountId) return
    // Retry if previously in running_normal. The encoding is
    // `ziniao:running_normal` or `ziniao:running_normal:<base64msg>`.
    const wasNormalMode = browserFetchError === 'ziniao:running_normal' || browserFetchError.startsWith('ziniao:running_normal:')
    setFetchingBrowsers(true); setZiniaoBrowsers([]); setSelectedBrowserOauth(''); setBrowserFetchError('')
    // Reset retry state unless this is a retry from running_normal
    if (!wasNormalMode) setZiniaoRetried(false)
    try {
      const browsers = await api.get(`/api/ziniao-accounts/${accountId}/browsers`)
      setZiniaoBrowsers(browsers)
      setZiniaoRetried(false)
      if (browsers.length === 0) setBrowserFetchError('no_profiles')
    } catch (e) {
      const msg = e instanceof Error ? e.message : ''
      // Try to parse structured JSON status from backend. The server
      // platform comes from /api/system/info (serverPlatform state) —
      // we only carry the ziniao status + its own error text here.
      try {
        const status = JSON.parse(msg)
        if (status.status) {
          if (wasNormalMode && status.status === 'running_normal') setZiniaoRetried(true)
          // Carry Ziniao's own err text through as an extra colon-
          // delimited field so the UI can surface it. base64 the
          // message so embedded colons / unicode don't tangle the
          // parser on the other side.
          const ziniaoMsg = (status.message || '').toString()
          const encoded = ziniaoMsg
            ? `:${btoa(unescape(encodeURIComponent(ziniaoMsg)))}`
            : ''
          setBrowserFetchError(`ziniao:${status.status}${encoded}`)
          setFetchingBrowsers(false)
          return
        }
      } catch { /* not JSON, fall through */ }
      // Fallback: existing string-matching logic
      if (msg.includes('/api/ziniao/launcher') || msg.includes('not running')) setBrowserFetchError('connect_error')
      else if (msg.includes('Ziniao API error') || msg.includes('-10003')) setBrowserFetchError('api_error:' + msg)
      else setBrowserFetchError('connect_error')
    }
    setFetchingBrowsers(false)
  }
  const restartZiniao = async (accountId: string) => {
    setFetchingBrowsers(true); setBrowserFetchError('')
    try {
      await api.post(`/api/ziniao-accounts/${accountId}/restart`)
      setZiniaoRetried(false)
      await fetchBrowserProfiles(accountId)
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Restart failed'
      setBrowserFetchError('restart_failed:' + msg)
    }
    setFetchingBrowsers(false)
  }

  // ─── Store helpers ─────────────────────────────────
  const createStore = async () => {
    if (!newStoreName.trim()) return
    if (newStoreBackend === 'ziniao' && (!selectedZiniaoAccountId || !selectedBrowserOauth)) { alert(t('settings.ziniaoSelectProfile')); return }
    const body: Record<string, unknown> = { name: newStoreName, browser_backend: newStoreBackend }
    if (newStoreBackend === 'ziniao') { body.ziniao_account_id = selectedZiniaoAccountId; body.browser_oauth = selectedBrowserOauth }
    if (newStoreBackend === 'chrome' && (newStoreProxyServer.trim() || newStoreProxyBypass.trim())) {
      const bc: Record<string, string> = {}
      if (newStoreProxyServer.trim()) bc.proxy_server = newStoreProxyServer.trim()
      if (newStoreProxyBypass.trim()) bc.proxy_bypass = newStoreProxyBypass.trim()
      body.browser_config = bc
    }
    await api.post('/api/stores', body)
    setNewStoreName(''); setNewStoreBackend('chrome'); setSelectedZiniaoAccountId(''); setZiniaoBrowsers([]); setSelectedBrowserOauth('')
    setShowProxy(false); setNewStoreProxyServer(''); setNewStoreProxyBypass(''); setShowCreateStore(false); await loadStores()
  }

  const selectStore = async (store: Store) => {
    setNavOpen(false)
    userActedRef.current = true; setSelectedStore(store); setShowAllTasks(false); setSelectedTask(null); setSteps([]); setScreenshots({}); setLogs([])
    setSelectedSchedule(null); setScheduleTasks([])
    setTasks(await api.get(`/api/tasks?store_id=${store.id}`))
    loadSchedules()
  }
  const selectAllTasks = async () => {
    setNavOpen(false)
    userActedRef.current = true; setSelectedStore(null); setShowAllTasks(true); setSelectedTask(null); setSteps([]); setScreenshots({}); setLogs([])
    setSelectedSchedule(null); setScheduleTasks([])
    setTasks(await api.get('/api/tasks?store_id=__none__'))
    loadSchedules()
  }

  const selectTask = async (task: Task) => {
    let fullTask = task
    try { fullTask = await api.get(`/api/tasks/${task.id}`) } catch { /* ignore */ }
    setSelectedTask(fullTask); setLogs([]); setScreenshots({}); setAgentMessages([])
    if (fullTask.todos) { try { setTodoItems(JSON.parse(fullTask.todos)) } catch { setTodoItems([]) } } else { setTodoItems([]) }
    setSelectedAnswers({}); setOtherInputs({}); setShowOtherInput({}); setChatInput('')
    setPendingQuestions(null)  // Clear immediately to avoid stale UI
    // Recover pending question if agent is waiting
    try {
      const q = await api.get(`/api/tasks/${fullTask.id}/questions/pending`)
      if (q.pending) { setPendingQuestions({ request_id: q.request_id, questions: q.questions }) }
      else { setPendingQuestions(null) }
    } catch { setPendingQuestions(null) }

    // Build conversation items from messages + task state
    const convItems: ConversationItem[] = []
    try {
      const msgs = await api.get(`/api/tasks/${task.id}/messages`)
      setAgentMessages(msgs.map((m: { role: string; content: string }) => ({ role: m.role, content: m.content })))
      let hasSeenResult = false
      for (const m of msgs as { role: string; content: string; created_at?: string }[]) {
        const ts = m.created_at || new Date().toISOString()
        if (m.role === 'user') {
          convItems.push({ id: `hist-user-${convItems.length}`, type: 'user_message', timestamp: ts, message: { role: 'user', content: m.content } })
        } else if (m.role === 'assistant') {
          convItems.push({ id: `hist-asst-${convItems.length}`, type: 'agent_message', timestamp: ts, message: { role: 'assistant', content: m.content } })
        } else if (m.role === 'result') {
          if (!hasSeenResult) {
            convItems.push({ id: `hist-result-${convItems.length}`, type: 'result', timestamp: ts, result: m.content })
            hasSeenResult = true
          } else {
            convItems.push({ id: `hist-asst-${convItems.length}`, type: 'agent_message', timestamp: ts, message: { role: 'assistant', content: m.content } })
          }
        } else if (m.role === 'tool_use') {
          try {
            const toolInfo = JSON.parse(m.content)
            convItems.push({ id: `hist-tool-${convItems.length}`, type: 'tool_call', timestamp: ts, toolCall: toolInfo })
          } catch { /* skip malformed */ }
        } else if (m.role === 'thinking') {
          convItems.push({ id: `hist-think-${convItems.length}`, type: 'thinking', timestamp: ts, thinking: { content: m.content, isStreaming: false } })
        }
      }
    } catch { /* ignore */ }
    if (fullTask.plan_history) {
      try {
        const history = JSON.parse(fullTask.plan_history) as { version: number; content: string; created_at: string }[]
        for (const h of history) {
          convItems.push({ id: `hist-plan-${h.version}`, type: 'plan', timestamp: h.created_at, plan: { version: h.version, content: h.content, isCurrent: h.version === history.length } })
        }
      } catch { /* fallback below */ }
    }
    if (!convItems.some(i => i.type === 'plan') && fullTask.plan) {
      convItems.push({ id: `hist-plan-1`, type: 'plan', timestamp: new Date().toISOString(), plan: { version: 1, content: fullTask.plan, isCurrent: true } })
    }
    // Add execution separator for tasks already in execute phase
    const execPhaseStatuses = ['running', 'waiting', 'completed', 'failed']
    if (execPhaseStatuses.includes(fullTask.status) && fullTask.plan) {
      convItems.push({ id: `hist-exec-sep`, type: 'execution_separator', timestamp: new Date().toISOString() })
    }
    // task.result is the authoritative result content (it may have
    // been resolved from a file pointer by routers/tasks.py
    // set_task_result; that resolution emits an SSE result event but
    // intentionally does NOT persist a TaskMessage). The persisted
    // role='result' messages are short CLI-transcript snippets and
    // must not win over task.result on history rebuild.
    if (fullTask.result) {
      const existingIdx = convItems.findIndex(i => i.type === 'result')
      const finalResult = {
        id: 'hist-result-final',
        type: 'result' as const,
        timestamp: new Date().toISOString(),
        result: fullTask.result,
      }
      if (existingIdx >= 0) {
        // Demote the persisted transcript-result to a regular agent
        // message so it's still visible in conversation history,
        // and put the canonical result in its place.
        const stale = convItems[existingIdx]
        convItems[existingIdx] = finalResult
        if (stale.type === 'result' && stale.result) {
          convItems.push({
            id: `hist-asst-from-stale-result-${convItems.length}`,
            type: 'agent_message',
            timestamp: stale.timestamp,
            message: { role: 'assistant', content: stale.result },
          })
        }
      } else {
        convItems.push(finalResult)
      }
    }
    setConversationItems(convItems)

    const stepsData = await api.get(`/api/tasks/${task.id}/steps`); setSteps(stepsData)
    for (const s of stepsData) {
      if (s.screenshot_id) {
        try {
          const resp = await fetch(`/api/screenshots/${s.screenshot_id}`, { credentials: 'include' })
          if (resp.ok) { const blob = await resp.blob(); const reader = new FileReader(); reader.onload = () => { const b64 = (reader.result as string).split(',')[1]; setScreenshots(prev => ({ ...prev, [s.id]: b64 })) }; reader.readAsDataURL(blob) }
        } catch { /* ignore */ }
      }
    }
  }

  const stopAgent = async () => { if (!selectedTask) return; try { await api.post(`/api/tasks/${selectedTask.id}/agent/stop`) } catch { /* ignore */ }; setPendingQuestions(null) }
  const handlerDeps = {
    api,
    profileId: selectedProfileId,
    setTasks,
    setSelectedTask,
    setScheduleTasks,
  }
  const retryTask = (taskId: string) =>
    retryTaskHandler(taskId, {
      ...handlerDeps,
      onCleared: () => {
        setSteps([]); setScreenshots({}); setAgentMessages([]); setTodoItems([]); setLogs([]); setConversationItems([]); setPendingQuestions(null); setSelectedAnswers({}); setOtherInputs({}); setShowOtherInput({})
      },
    })
  const continueTask = (taskId: string) =>
    continueTaskHandler(taskId, handlerDeps)
  const deleteTask = async (taskId: string) => {
    let childCount = 0
    try {
      const kids = await api.get(`/api/tasks?parent_task_id=${encodeURIComponent(taskId)}`) as Task[]
      childCount = Array.isArray(kids) ? kids.length : 0
    } catch { /* ignore — fall back to plain confirm */ }
    const message = childCount > 0
      ? t('tasks.deleteConfirmCascade', { count: childCount })
      : t('tasks.deleteConfirm')
    if (!confirm(message)) return
    try {
      await api.del(`/api/tasks/${taskId}`)
      // Locally drop the deleted task plus any descendants we
      // know about (cascade on the server won't push individual
      // SSE deletes for each child).
      const dropIds = new Set<string>([taskId])
      let frontier = [taskId]
      while (frontier.length) {
        const next: string[] = []
        for (const id of frontier) {
          for (const t2 of tasks) if (t2.parent_task_id === id) next.push(t2.id)
          for (const t2 of scheduleTasks) if (t2.parent_task_id === id) next.push(t2.id)
        }
        // Filter to unvisited BEFORE adding to dropIds — otherwise
        // every BFS frontier collapses to [] after the first hop and
        // we never reach grandchildren.
        const unvisited = next.filter(id => !dropIds.has(id))
        unvisited.forEach(id => dropIds.add(id))
        frontier = unvisited
      }
      setTasks(prev => prev.filter(x => !dropIds.has(x.id)))
      setScheduleTasks(prev => prev.filter(x => !dropIds.has(x.id)))
      if (selectedTask && dropIds.has(selectedTask.id)) {
        setSelectedTask(null)
        setSteps([]); setScreenshots({}); setLogs([]); setConversationItems([])
        setAgentMessages([]); setTodoItems([]); setPendingQuestions(null)
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err)
      if (msg !== 'unauthorized') alert(msg)
    }
  }
  const selectAnswer = (questionText: string, answer: string) => { setSelectedAnswers(prev => ({ ...prev, [questionText]: answer })); setShowOtherInput(prev => ({ ...prev, [questionText]: false })); setOtherInputs(prev => ({ ...prev, [questionText]: '' })) }
  const toggleOtherInput = (questionText: string) => { setShowOtherInput(prev => { const show = !prev[questionText]; if (show) { setSelectedAnswers(p => { const n = { ...p }; delete n[questionText]; return n }) } else { setOtherInputs(p => ({ ...p, [questionText]: '' })); setSelectedAnswers(p => { const n = { ...p }; delete n[questionText]; return n }) } return { ...prev, [questionText]: show } }) }
  const setOtherAnswer = (questionText: string, text: string) => { setOtherInputs(prev => ({ ...prev, [questionText]: text })); if (text.trim()) { setSelectedAnswers(prev => ({ ...prev, [questionText]: text.trim() })) } else { setSelectedAnswers(prev => { const n = { ...prev }; delete n[questionText]; return n }) } }
  const submitAllAnswers = async (overrideAnswers?: Record<string, string>) => { if (!selectedTask || !pendingQuestions) return; const answers = overrideAnswers || selectedAnswers; await api.post(`/api/tasks/${selectedTask.id}/questions/answer`, { request_id: pendingQuestions.request_id, answers }); setPendingQuestions(null); setSelectedAnswers({}); setOtherInputs({}); setShowOtherInput({}) }
  const sendingRef = useRef(false)
  const sendChatMessage = async () => {
    if (!selectedTask || !chatInput.trim() || sendingRef.current) return
    sendingRef.current = true
    const content = chatInput.trim(); setChatInput('')
    sendEvent(FrontendEvent.TASK_MESSAGE_SUBMITTED, {
      length_bucket: lengthBucket(content.length),
      task_status_at_send: selectedTask.status,
      is_first_message_for_task: !conversationItems.some(c => c.type === 'user_message'),
    })
    // Optimistic add to both agentMessages (debug) and conversationItems
    setAgentMessages(prev => [...prev, { role: 'user', content }])
    setConversationItems(prev => [...prev, {
      id: `user-opt-${Date.now()}`,
      type: 'user_message',
      timestamp: new Date().toISOString(),
      message: { role: 'user', content },
    }])
    try {
      const response = await api.post(`/api/tasks/${selectedTask.id}/messages`, { content, profile_id: selectedProfileId })
      if (response.woken) {
        setSelectedTask(prev => prev ? { ...prev, status: 'queued' } : prev)
        setTasks(prev => prev.map(t2 => t2.id === selectedTask.id ? { ...t2, status: 'queued' } : t2))
      }
      if (response.profile_switched) {
        setSelectedTask(prev => prev ? { ...prev, ai_profile_id: selectedProfileId } : prev)
      }
    } catch (err) { console.error('Failed to send message:', err) } finally { sendingRef.current = false }
  }

  // ─── Profile helpers ───────────────────────────────
  const createProfile = async (profile: Omit<Profile, 'id'>) => { const newProfile = await api.post('/api/profiles', profile); setProfiles(prev => [...prev, newProfile]); return newProfile }
  const updateProfile = async (id: string, profile: Omit<Profile, 'id'>) => { const updated = await api.put(`/api/profiles/${id}`, profile); setProfiles(prev => prev.map(p => p.id === id ? updated : p)); return updated }
  const deleteProfile = async (id: string) => { await api.del(`/api/profiles/${id}`); setProfiles(prev => prev.filter(p => p.id !== id)); if (selectedProfileId === id) setSelectedProfileId('default') }

  // ─── Workspace helpers ─────────────────────────────
  // ─── Workspace assistant helpers ────────────────────
  const sendWsAssistantMessage = async (content: string) => {
    setWsAssistantMessages(prev => [...prev, { role: 'user', content }])
    setWsAssistantRunning(true)
    try { await api.post('/api/workspace/assistant/message', { content, profile_id: selectedProfileId }) } catch { setWsAssistantRunning(false) }
  }
  const stopWsAssistant = async () => {
    try { await api.post('/api/workspace/assistant/stop') } catch { /* ignore */ }
    setWsAssistantRunning(false)
  }

  const { openWsFile, saveWsFile, syncProjectKnowledge, syncBuiltinSkills, loadFileHistory, previewVersion, resetFileToVersion, createWsFile, deleteWsFile } = useWsFiles({
    t, wsSelectedFile, wsEditorContent, wsSaving,
    setWsSelectedFile, setWsFileContent, setWsEditorContent, setWsEditorDirty, setWsShowHistory,
    setWsPreviewCommit, setWsPreviewContent, setWsSaving, setWsSyncing, setWsSyncMeta,
    setWsSkillsSyncing, setWsFileHistory, setWsNewFileName, setWsNewFileSection, loadWsStructured,
  })
  const toggleStoreExpanded = (slug: string) => { setWsExpandedStores(prev => { const next = new Set(prev); if (next.has(slug)) next.delete(slug); else next.add(slug); return next }) }
  const toggleSkillExpanded = (slug: string) => { setWsExpandedSkills(prev => { const next = new Set(prev); if (next.has(slug)) next.delete(slug); else next.add(slug); return next }) }

  // ─── Auth helpers ──────────────────────────────────
  const handleLogin = async () => { setLoginError(''); try { const r = await fetch('/api/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ identifier: loginIdentifier, password: loginPassword }), credentials: 'include' }); if (!r.ok) { setLoginError(t('auth.invalidCredentials')); return }; const u = await r.json(); setCurrentUser(u); if (u.default_profile_id) setSelectedProfileId(u.default_profile_id); setDebugMode(u.debug_mode ?? false); setLoginIdentifier(''); setLoginPassword('') } catch { setLoginError(t('auth.invalidCredentials')) } }
  // Reset session-scoped selection on logout so next login re-enters
  // the default-selection branch (else a stale selectedStore skips it).
  const handleLogout = async () => { await fetch('/api/auth/logout', { method: 'POST', credentials: 'include' }); setCurrentUser(null); setSelectedStore(null); setShowAllTasks(true); setSelectedTask(null); setSelectedSchedule(null); setAppView('tasks') }

  // ─── Settings helpers ──────────────────────────────
  const createUser = async () => { if (!newUserForm.username.trim() || !newUserForm.password.trim()) return; try { await api.post('/api/users', { ...newUserForm, email: newUserForm.email.trim() || undefined }); setNewUserForm({ username: '', email: '', password: '', role: 'member' }); setShowAddUser(false); await loadUsers() } catch { /* ignore */ } }
  const deleteUser = async (userId: string, username: string) => { if (!confirm(t('settings.confirmDeleteUser', { username }))) return; try { await api.del(`/api/users/${userId}`); await loadUsers() } catch { /* ignore */ } }

  // ─── Side effects ──────────────────────────────────
  useEffect(() => { if (appView === 'workspace') { loadWsStructured(); loadSyncMeta() } else { stopWsAssistant() } }, [appView])
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { if (appView === 'settings') { loadEmailAccounts() } }, [appView])

  // SSE
  useSSE({
    selectedTaskId: selectedTask?.id, appView,
    selectedStoreId: selectedStore?.id ?? null,
    showAllTasks,
    setTasks, setSelectedTask, setSteps, setScreenshots, setAgentMessages, setTodoItems,
    setConversationItems,
    setPendingQuestions, setSelectedAnswers, setOtherInputs, setShowOtherInput, setLogs,
    questionBannerRef, setScheduleTasks,
    loadScheduleTasks: () => {
      if (selectedSchedule) api.get(`/api/schedules/${selectedSchedule.id}/tasks`).then(setScheduleTasks).catch(() => {})
    },
    loadSchedules,
    loadTasks: () => {
      if (selectedStore) api.get(`/api/tasks?store_id=${selectedStore.id}`).then(setTasks).catch(() => {})
      else if (showAllTasks) api.get('/api/tasks?store_id=__none__').then(setTasks).catch(() => {})
    },
    setWsAssistantMessages,
    setWsAssistantRunning,
    loadWsStructured,
  })

  const taskPanelActive = selectedStore || showAllTasks
  const taskPanelTitle = showAllTasks ? t('tasks.allStores') : selectedStore?.name || ''

  // ─── Login / loading ───────────────────────────────
  if (!authChecked) return <div className="flex items-center justify-center h-screen bg-gray-100 text-gray-500">{t('common.loading')}</div>
  if (!currentUser) return <LoginPage loginIdentifier={loginIdentifier} setLoginIdentifier={setLoginIdentifier} loginPassword={loginPassword} setLoginPassword={setLoginPassword} loginError={loginError} onLogin={handleLogin} />

  const submitCreateTask = async (title: string, description: string, files: PendingFile[], platform?: string, country?: string) => {
    await submitCreateTaskHandler(
      { title, description, files, platform, country },
      {
        api,
        storeId: selectedStore?.id || null,
        planMode: selectedStore ? (currentUser?.plan_mode_default ?? false) : true,
        setTasks,
        setSelectedTask,
        uploadAttachment: async (taskId, pf) => {
          const form = new FormData()
          form.append('file', pf.file)
          await fetch(`/api/attachments/${taskId}`, { method: 'POST', body: form, credentials: 'include' })
        },
        onCreated: () => {
          setSteps([]); setScreenshots({}); setLogs([]); setConversationItems([]); setAgentMessages([]); setTodoItems([]); setPendingQuestions(null); setSelectedAnswers({}); setOtherInputs({}); setShowOtherInput({}); setChatInput('')
        },
      },
    )
  }

  return (
    <div className="flex h-screen bg-gray-100 text-gray-900">
      {/* Mobile drawer scrim — tap to close the slide-in sidebar. */}
      {isMobile && navOpen && (
        <div
          className="fixed inset-0 z-40 bg-black/40"
          onClick={() => setNavOpen(false)}
          aria-hidden
        />
      )}
      <Sidebar
        isMobile={isMobile} navOpen={navOpen} closeNav={() => setNavOpen(false)}
        currentUser={currentUser} appView={appView} setAppView={setAppView} handleLogout={handleLogout} authRequired={authRequired}
        stores={stores} selectedStore={selectedStore} showAllTasks={showAllTasks}
        selectStore={selectStore} selectAllTasks={selectAllTasks}
        showCreateStore={showCreateStore} setShowCreateStore={setShowCreateStore} createStore={createStore}
        newStoreName={newStoreName} setNewStoreName={setNewStoreName}
        newStoreBackend={newStoreBackend} setNewStoreBackend={setNewStoreBackend}
        showProxy={showProxy} setShowProxy={setShowProxy}
        newStoreProxyServer={newStoreProxyServer} setNewStoreProxyServer={setNewStoreProxyServer}
        newStoreProxyBypass={newStoreProxyBypass} setNewStoreProxyBypass={setNewStoreProxyBypass}
        ziniaoAccounts={ziniaoAccounts} selectedZiniaoAccountId={selectedZiniaoAccountId} setSelectedZiniaoAccountId={setSelectedZiniaoAccountId}
        ziniaoBrowsers={ziniaoBrowsers} selectedBrowserOauth={selectedBrowserOauth} setSelectedBrowserOauth={setSelectedBrowserOauth}
        fetchingBrowsers={fetchingBrowsers} browserFetchError={browserFetchError} setBrowserFetchError={setBrowserFetchError}
        fetchBrowserProfiles={fetchBrowserProfiles} restartZiniao={restartZiniao} ziniaoRetried={ziniaoRetried}
        serverPlatform={serverPlatform} serverVersion={serverVersion}
        showAddAccount={showAddAccount} setShowAddAccount={setShowAddAccount}
        showAccountPassword={showAccountPassword} setShowAccountPassword={setShowAccountPassword}
        editingAccountId={editingAccountId} setEditingAccountId={setEditingAccountId}
        newAccount={newAccount} setNewAccount={setNewAccount}
        createZiniaoAccount={createZiniaoAccount} updateZiniaoAccount={updateZiniaoAccount} deleteZiniaoAccount={deleteZiniaoAccount}
        wsStructured={wsStructured} wsSelectedFile={wsSelectedFile}
        wsExpandedStores={wsExpandedStores} wsExpandedSkills={wsExpandedSkills}
        toggleStoreExpanded={toggleStoreExpanded} toggleSkillExpanded={toggleSkillExpanded}
        openWsFile={openWsFile} deleteWsFile={deleteWsFile}
        wsNewFileName={wsNewFileName} setWsNewFileName={setWsNewFileName}
        wsNewFileSection={wsNewFileSection} setWsNewFileSection={setWsNewFileSection}
        createWsFile={createWsFile} syncProjectKnowledge={syncProjectKnowledge}
        wsSyncing={wsSyncing} wsSyncMeta={wsSyncMeta} loadWsStructured={loadWsStructured}
        syncBuiltinSkills={syncBuiltinSkills} wsSkillsSyncing={wsSkillsSyncing}
      />

      {/* Main content area */}
      {appView === 'tasks' ? (
        <TasksView
          isMobile={isMobile} onOpenNav={() => setNavOpen(true)}
          taskPanelActive={!!taskPanelActive} taskPanelTitle={taskPanelTitle}
          tasks={tasks} selectedTask={selectedTask} steps={steps} screenshots={screenshots} logs={logs}
          agentMessages={agentMessages} todoItems={todoItems} pendingQuestions={pendingQuestions}
          conversationItems={conversationItems}
          selectedAnswers={selectedAnswers} otherInputs={otherInputs} showOtherInput={showOtherInput}
          chatInput={chatInput} setChatInput={setChatInput} debugMode={debugMode} setDebugMode={setDebugMode}
          profiles={profiles} selectedProfileId={selectedProfileId} setSelectedProfileId={setSelectedProfileId}
          currentUser={currentUser} showAllTasks={showAllTasks}
          openCreateModal={() => setShowCreateTask(true)} selectTask={selectTask}
          stopAgent={stopAgent} retryTask={retryTask} continueTask={continueTask} deleteTask={deleteTask}
          selectAnswer={selectAnswer} toggleOtherInput={toggleOtherInput}
          setOtherAnswer={setOtherAnswer} submitAllAnswers={submitAllAnswers} sendChatMessage={sendChatMessage}
          setSelectedTask={setSelectedTask} setTasks={setTasks} setCurrentUser={setCurrentUser}
          setEditingProfile={setEditingProfile} setShowProfileModal={setShowProfileModal}
          questionBannerRef={questionBannerRef}
          taskSubTab={taskSubTab} setTaskSubTab={setTaskSubTab}
          schedules={schedules} selectedSchedule={selectedSchedule} scheduleTasks={scheduleTasks}
          showCreateSchedule={showCreateSchedule} setShowCreateSchedule={setShowCreateSchedule}
          selectSchedule={selectSchedule} deleteSchedule={deleteSchedule}
          toggleSchedulePause={toggleSchedulePause} triggerSchedule={triggerSchedule}
          replanSchedule={replanSchedule}
          setSelectedSchedule={setSelectedSchedule}
          onScheduleUpdated={onScheduleUpdated}
          selectedStore={selectedStore}
          stores={stores}
        />
      ) : appView === 'workspace' ? (
        <div className="flex-1 flex flex-col min-w-0">
          {/* Mode toggle header */}
          <div className="px-4 py-2.5 bg-white border-b border-gray-100 flex items-center justify-end gap-2">
            {isMobile && (
              <button
                onClick={() => setNavOpen(true)}
                className="mr-auto w-9 h-9 -ml-1 flex items-center justify-center rounded-lg text-gray-500 hover:bg-gray-100"
                aria-label={t('common.menu', 'Menu')}
              >
                <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 12h16M4 18h16" /></svg>
              </button>
            )}
            <div className="inline-flex rounded-lg border border-gray-200 p-1 gap-1">
              <button
                onClick={() => setWsAssistantActive(false)}
                className={`px-4 py-1.5 rounded-md transition-colors flex flex-col items-center ${!wsAssistantActive ? 'bg-gray-100 text-gray-800' : 'text-gray-400 hover:text-gray-600 hover:bg-gray-50'}`}
              >
                <span className="text-sm font-medium">{t('workspace.modeFiles')}</span>
                <span className="text-[10px] opacity-60">{t('workspace.modeFilesSub')}</span>
              </button>
              <button
                onClick={() => setWsAssistantActive(true)}
                className={`px-4 py-1.5 rounded-md transition-colors flex flex-col items-center ${wsAssistantActive ? 'bg-indigo-50 text-indigo-600' : 'text-gray-400 hover:text-gray-600 hover:bg-gray-50'}`}
              >
                <span className="text-sm font-medium">{t('workspace.modeAI')}</span>
                <span className="text-[10px] opacity-60">{t('workspace.modeAISub')}</span>
              </button>
            </div>
          </div>
          {wsAssistantActive ? (
            <WorkspaceAssistantView
              messages={wsAssistantMessages}
              isRunning={wsAssistantRunning}
              onSendMessage={sendWsAssistantMessage}
              onStop={stopWsAssistant}
            />
          ) : (
            <WorkspaceView
              wsSelectedFile={wsSelectedFile} wsEditorContent={wsEditorContent} wsFileContent={wsFileContent}
              wsEditorDirty={wsEditorDirty} wsSaving={wsSaving} wsShowHistory={wsShowHistory}
              wsFileHistory={wsFileHistory} wsPreviewCommit={wsPreviewCommit} wsPreviewContent={wsPreviewContent}
              wsStructured={wsStructured}
              setWsEditorContent={setWsEditorContent} setWsEditorDirty={setWsEditorDirty}
              setWsShowHistory={setWsShowHistory} setWsPreviewCommit={setWsPreviewCommit}
              loadFileHistory={loadFileHistory} previewVersion={previewVersion}
              resetFileToVersion={resetFileToVersion} deleteWsFile={deleteWsFile} saveWsFile={saveWsFile}
            />
          )}
        </div>
      ) : (
        <div className="flex-1 flex flex-col min-w-0">
        {isMobile && (
          <div className="px-3 py-2 bg-white border-b border-gray-200 flex items-center">
            <button
              onClick={() => setNavOpen(true)}
              className="w-9 h-9 -ml-1 flex items-center justify-center rounded-lg text-gray-500 hover:bg-gray-100"
              aria-label={t('common.menu', 'Menu')}
            >
              <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 12h16M4 18h16" /></svg>
            </button>
            <span className="ml-1 font-semibold text-gray-800">{t('settings.title')}</span>
          </div>
        )}
        <SettingsView
          currentUser={currentUser} settingsTab={settingsTab} setSettingsTab={setSettingsTab}
          allUsers={allUsers} showAddUser={showAddUser} setShowAddUser={setShowAddUser}
          newUserForm={newUserForm} setNewUserForm={setNewUserForm} createUser={createUser} deleteUser={deleteUser}
          profiles={profiles} setEditingProfile={setEditingProfile} setShowProfileModal={setShowProfileModal}
          setCurrentUser={setCurrentUser} setSelectedProfileId={setSelectedProfileId} deleteProfile={deleteProfile}
          emailAccounts={emailAccounts} loadEmailAccounts={loadEmailAccounts}
          stores={stores} loadStores={loadStores}
          authRequired={authRequired} setAuthRequired={setAuthRequired} loadUsers={loadUsers}
        />
        </div>
      )}

      {/* Modals */}
      {showCreateTask && (
        <CreateTaskModal
          showAllTasks={showAllTasks} storeName={selectedStore?.name}
          selectedStore={selectedStore}
          onClose={() => setShowCreateTask(false)} onSubmit={submitCreateTask}
        />
      )}
      {showCreateSchedule && (
        <CreateScheduleModal
          storeId={selectedStore?.id || null}
          storeName={selectedStore?.name}
          onClose={() => setShowCreateSchedule(false)}
          onCreated={(schedule) => { setSchedules(prev => [schedule, ...prev]); setSelectedSchedule(schedule) }}
        />
      )}
      {showProfileModal && (
        <ProfileModal
          isOpen={showProfileModal}
          onClose={() => setShowProfileModal(false)}
          onSave={async (profile) => {
            if (editingProfile) { await updateProfile(editingProfile.id, profile) } else { const newProfile = await createProfile(profile); if (profiles.length === 0) setSelectedProfileId(newProfile.id) }
            setShowProfileModal(false)
          }}
          editingProfile={editingProfile}
        />
      )}
      {updateCheck && (
        <UpdateAvailableModal result={updateCheck} onClose={dismissUpdateCheck} />
      )}
    </div>
  )
}
