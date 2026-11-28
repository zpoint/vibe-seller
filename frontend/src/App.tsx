import { useState, useEffect, useRef } from 'react'
import { useNavigate, useRouterState } from '@tanstack/react-router'
import { useTranslation } from 'react-i18next'
import { ProfileModal } from './components/ProfileModal'
import { LoginPage } from './components/LoginPage'
import { Sidebar } from './components/Sidebar'
import { MobileMenuButton } from './components/MobileMenuButton'
import { useWsFiles } from './hooks/useWsFiles'
import { useUpdateFlow } from './hooks/useUpdateFlow'
import { useSessionKeepalive } from './hooks/useSessionKeepalive'
import { useSessionBoundary } from './hooks/useSessionBoundary'
import { useBrowserVendor } from './hooks/useBrowserVendor'
import { useBrowserVendors } from './hooks/useBrowserVendors'
import { useZiniaoAccounts } from './hooks/useZiniaoAccounts'
import { AttentionBanner } from './components/AttentionBanner'
import { scrollToSection } from './hooks/useScrollIntoViewOnHash'
import { useServerInfo } from './hooks/useServerInfo'
import { useIsMobile } from './hooks/useIsMobile'
import { useMobileBackStack } from './hooks/useMobileBackStack'
import { CreateTaskModal } from './components/CreateTaskModal'
import { CreateScheduleModal } from './components/CreateScheduleModal'
import { UpdateFlow } from './components/UpdateFlow'
import { TasksView } from './views/TasksView'
import { WorkspaceView } from './views/WorkspaceView'
import { WorkspaceAssistantView } from './views/WorkspaceAssistantView'
import { SettingsView, type SettingsTab } from './views/SettingsView'
import { useSSE } from './hooks/useSSE'
import { createDraftStore } from './lib/draftStore'
import { parseNav, settingsTabToSlug, listTarget, scheduleTarget, taskTarget } from './lib/route'
import { resolveLanding } from './lib/landing'
import { useTaskOpener } from './hooks/useTaskOpener'
import { useTaskActions } from './hooks/useTaskActions'
import { api, uploadTaskAttachment } from './api'
import { createStore as createStoreHandler } from './handlers/createStore'
import { triggerSchedule as triggerScheduleHandler } from './handlers/triggerSchedule'
import { replanSchedule as replanScheduleHandler } from './handlers/replanSchedule'
import { selectSchedule as selectScheduleHandler } from './handlers/selectSchedule'
import { submitCreateTask as submitCreateTaskHandler } from './handlers/submitCreateTask'
import { sendChatMessage as sendChatMessageHandler } from './handlers/sendChatMessage'
import { useAiSetupGate } from './hooks/useAiSetupGate'
import type { Store, Task, TaskStep, AgentMessage, TodoItem, AuthUser, Profile, WsStructured, AppView, PendingFile, Schedule, EmailAccount, ConversationItem, StagedAttachment } from './types'

export default function App() {
  const { t } = useTranslation()

  const { serverPlatform, serverVersion, installId } = useServerInfo()

  // View + settings-tab are now URL-derived (TanStack Router), not
  // useState — so every section has its own address (Back / deep-link /
  // bookmark work). `setAppView`/`setSettingsTab` are kept as the same
  // callable props the children already take, but they navigate.
  const navigate = useNavigate()
  const pathname = useRouterState({ select: (s) => s.location.pathname })
  const nav = parseNav(pathname)
  const appView = nav.appView
  const setAppView = (v: AppView) => {
    navigate({ to: v === 'workspace' ? '/workspace' : v === 'settings' ? '/settings' : '/tasks' })
  }
  // Mobile: the sidebar collapses into a slide-in drawer toggled by a
  // hamburger; desktop keeps it as a resident column.
  const isMobile = useIsMobile()
  const [navOpen, setNavOpen] = useState(false)
  const [stores, setStores] = useState<Store[]>([])
  const [selectedStore, setSelectedStore] = useState<Store | null>(null)
  const [showAllTasks, setShowAllTasks] = useState(true)
  // On the '/' landing no scope is selected yet, so the All Stores
  // highlight, its panel and its list stay off until the URL resolves.
  const showAllScope = showAllTasks && !nav.landing
  const [tasks, setTasks] = useState<Task[]>([])
  const [tasksLoading, setTasksLoading] = useState(false)
  // Key ('<store_id>' or '__none__') of the task-list fetch currently
  // in flight. Stamped when a store/all-stores view is selected; the
  // response and its loading toggle are dropped if the ref has since
  // changed, so a slow response for the previous store can't clobber
  // the newly-selected store's list (stale-response-wins race).
  const inFlightTasksKeyRef = useRef<string | null>(null)
  const [selectedTask, setSelectedTask] = useState<Task | null>(null)
  const [steps, setSteps] = useState<TaskStep[]>([])
  const [screenshots, setScreenshots] = useState<Record<string, string>>({})
  const [logs, setLogs] = useState<string[]>([])
  const [newStoreName, setNewStoreName] = useState('')
  const [newStoreBackend, setNewStoreBackend] = useState('chrome')
  const [showCreateStore, setShowCreateStore] = useState(false)
  const [creatingStore, setCreatingStore] = useState(false)
  const [createStoreError, setCreateStoreError] = useState('')
  // State disables the button; the ref also closes the same-tick gap before
  // React re-renders, so Enter + click can never issue two POSTs.
  const createStoreInFlight = useRef(false)
  const setCreateStoreOpen = (open: boolean) => {
    setShowCreateStore(open)
    setCreateStoreError('')
  }

  // Ziniao account state (hooks/useZiniaoAccounts)
  const ziniao = useZiniaoAccounts()
  const { selectedZiniaoAccountId, setSelectedZiniaoAccountId, setZiniaoBrowsers, selectedBrowserOauth, setSelectedBrowserOauth, loadZiniaoAccounts } = ziniao
  const vendorBrowser = useBrowserVendor(newStoreBackend)
  const [showCreateTask, setShowCreateTask] = useState(false)
  const aiGate = useAiSetupGate()

  // Agent state
  const [agentMessages, setAgentMessages] = useState<AgentMessage[]>([])
  const [todoItems, setTodoItems] = useState<TodoItem[]>([])
  const [pendingQuestions, setPendingQuestions] = useState<{ request_id: string; questions: { header?: string; question: string; options?: { label: string; description?: string }[] }[] } | null>(null)
  const [selectedAnswers, setSelectedAnswers] = useState<Record<string, string>>({})
  const [otherInputs, setOtherInputs] = useState<Record<string, string>>({})
  const [showOtherInput, setShowOtherInput] = useState<Record<string, boolean>>({})
  // The composer draft lives in a store, not App state: as state, every
  // keystroke re-rendered the whole app. App clears it (task open /
  // create / send) and reads it on send; only the composer subscribes.
  const [chatDraft] = useState(() => createDraftStore())
  const setChatInput = chatDraft.set
  const [chatAttachments, setChatAttachments] = useState<StagedAttachment[]>([])
  // One in-flight guard per task: a send on B is not blocked by A's.
  const sendingRefs = useRef(new Map<string, { current: boolean }>())
  const sendingRefFor = (id: string) => {
    if (!sendingRefs.current.has(id)) sendingRefs.current.set(id, { current: false })
    return sendingRefs.current.get(id)!
  }
  const [conversationItems, setConversationItems] = useState<ConversationItem[]>([])
  const questionBannerRef = useRef<HTMLDivElement>(null)
  const [debugMode, setDebugMode] = useState(false)
  // Opens the route's task and tracks the load (pending / failed).
  // What is on screen, for a task load to keep the cards that arrive live
  // while its fetches are in flight.
  const conversationItemsRef = useRef(conversationItems)
  conversationItemsRef.current = conversationItems
  const pendingQuestionsRef = useRef(pendingQuestions)
  pendingQuestionsRef.current = pendingQuestions
  const taskOpener = useTaskOpener({
    setSelectedTask, setLogs, setScreenshots, setAgentMessages, setTodoItems,
    setSelectedAnswers, setOtherInputs, setShowOtherInput, setChatInput,
    setChatAttachments, setPendingQuestions, setConversationItems, setSteps,
    liveItems: () => conversationItemsRef.current,
    livePendingQuestions: () => pendingQuestionsRef.current,
  })
  const loadTaskById = taskOpener.open

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
  // taskSubTab is URL-derived (see above): a scope's tasks vs schedules route.
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
  // URL-derived: /settings/$tab (SettingsView keeps its identical props;
  // slug↔id mapping lives in lib/route). Setter navigates.
  const settingsTab = nav.settingsTab
  const setSettingsTab = (tab: SettingsTab) => {
    navigate({ to: '/settings/$tab', params: { tab: settingsTabToSlug(tab) } })
  }
  // A link into Settings that names a section (a platform's row) opens
  // the tab scrolled to it.
  const openSettings = (tab: SettingsTab, anchor?: string) => {
    navigate({ to: '/settings/$tab', params: { tab: settingsTabToSlug(tab) }, hash: anchor })
    if (anchor) scrollToSection(anchor)
  }

  // Selection is URL-derived too: the open task / store / schedule are
  // route params, so they deep-link and Back closes them. The rich
  // objects still live in state (loaded on param change); these are the
  // ids the effects below reconcile against.
  const routeTaskId = nav.taskId
  const routeStoreId = nav.storeId
  const routeScheduleId = nav.scheduleId
  const taskSubTab = nav.taskSubTab
  // Sub-tabs flip INSIDE the current scope (listTarget) — a store is
  // never dropped; the flat pair is only the all-stores one.
  const setTaskSubTab = (tab: 'onetime' | 'scheduled') => navigate(listTarget(routeStoreId, tab === 'scheduled'))
  const {
    currentUser, setCurrentUser, authChecked, authRequired, setAuthRequired,
    loginIdentifier, setLoginIdentifier, loginPassword, setLoginPassword,
    loginError, handleLogin, handleLogout,
  } = useSessionBoundary({
    t, navigate, taskOpener, ziniao, vendorBrowser, chatDraft,
    inFlightTasksKeyRef, inFlightScheduleIdRef, sendingRefs, createStoreInFlight,
    setters: {
      navOpen: setNavOpen, stores: setStores, selectedStore: setSelectedStore, showAllTasks: setShowAllTasks,
      tasks: setTasks, tasksLoading: setTasksLoading, selectedTask: setSelectedTask,
      steps: setSteps, screenshots: setScreenshots, logs: setLogs,
      newStoreName: setNewStoreName, newStoreBackend: setNewStoreBackend, showCreateStore: setShowCreateStore,
      creatingStore: setCreatingStore, createStoreError: setCreateStoreError, showCreateTask: setShowCreateTask,
      agentMessages: setAgentMessages, todoItems: setTodoItems, pendingQuestions: setPendingQuestions,
      selectedAnswers: setSelectedAnswers, otherInputs: setOtherInputs, showOtherInput: setShowOtherInput,
      chatAttachments: setChatAttachments, conversationItems: setConversationItems, debugMode: setDebugMode,
      profiles: setProfiles, selectedProfileId: setSelectedProfileId, showProfileModal: setShowProfileModal,
      editingProfile: setEditingProfile, wsAssistantActive: setWsAssistantActive,
      wsAssistantMessages: setWsAssistantMessages, wsAssistantRunning: setWsAssistantRunning,
      wsStructured: setWsStructured, wsSelectedFile: setWsSelectedFile, wsFileContent: setWsFileContent,
      wsEditorContent: setWsEditorContent, wsEditorDirty: setWsEditorDirty, wsSaving: setWsSaving,
      wsExpandedStores: setWsExpandedStores, wsExpandedSkills: setWsExpandedSkills,
      wsNewFileName: setWsNewFileName, wsNewFileSection: setWsNewFileSection, wsSyncMeta: setWsSyncMeta,
      wsSyncing: setWsSyncing, wsSkillsSyncing: setWsSkillsSyncing, wsFileHistory: setWsFileHistory,
      wsShowHistory: setWsShowHistory, wsPreviewCommit: setWsPreviewCommit, wsPreviewContent: setWsPreviewContent,
      showProxy: setShowProxy, newStoreProxyServer: setNewStoreProxyServer, newStoreProxyBypass: setNewStoreProxyBypass,
      schedules: setSchedules, selectedSchedule: setSelectedSchedule, scheduleTasks: setScheduleTasks,
      showCreateSchedule: setShowCreateSchedule, emailAccounts: setEmailAccounts, allUsers: setAllUsers,
      showAddUser: setShowAddUser, newUserForm: setNewUserForm,
    },
  })

  // Registry metadata reloads with the auth identity (PR #364).
  const vendors = useBrowserVendors(currentUser?.id ?? null)
  const updateFlow = useUpdateFlow(currentUser)

  // Roll the session forward on activity and bounce to login on idle
  // expiry — only meaningful when auth is on (otherwise never 401s).
  useSessionKeepalive(authChecked && !!currentUser && authRequired)

  // Mobile: device Back closes the nav drawer. Task/schedule detail are
  // routes now, so their Back is handled by the router.
  useMobileBackStack({
    isMobile, navOpen,
    closeNav: () => setNavOpen(false),
  })

  const debugInitialized = useRef(false)
  useEffect(() => { if (!debugInitialized.current) { debugInitialized.current = true; return } if (currentUser) api.patch('/api/auth/me/debug-mode', { debug_mode: debugMode }).catch(() => {}) }, [debugMode])
  useEffect(() => {
    if (!currentUser) return
    // Populate the sidebar. The bare '/' landing resolves off the store
    // load: first store, else /tasks (guard + rationale: lib/landing.ts).
    loadZiniaoAccounts(); loadProfiles()
    resolveLanding(loadStores(), store => navigate(store
      ? { to: '/stores/$storeId', params: { storeId: store.id }, replace: true }
      : { to: '/tasks', replace: true }))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentUser])

  // ─── URL → selection reconciliation ────────────────
  // The URL is the source of truth for what's open. Each effect loads
  // the rich object when its route param appears and clears it when the
  // param is gone (Back / navigating away). Guards prevent reload loops.
  // List scope from the URL alone, no fallback to stale state: any
  // /stores/$id… form (tasks or schedules) is that store; the no-store
  // tasks view is 'all' on BOTH sub-tabs; '/' (landing) loads nothing.
  // loadedScopeRef fires per key, so a sub-tab flip reloads no list.
  const listScopeKey = routeStoreId
    ? `store:${routeStoreId}`
    : appView === 'tasks' && !nav.landing
      ? 'all'
      : ''
  const loadedScopeRef = useRef<string>('')
  useEffect(() => {
    // Session dropped: forget the loaded scope so the next login re-selects.
    if (!currentUser) { loadedScopeRef.current = ''; return }
    if (!listScopeKey) return
    if (loadedScopeRef.current === listScopeKey) return
    if (routeStoreId) {
      const s = stores.find(x => x.id === routeStoreId)
      if (!s) return // stores not loaded yet — reruns when `stores` arrives
      loadedScopeRef.current = listScopeKey
      selectScope(s)
    } else {
      loadedScopeRef.current = listScopeKey
      selectScope(null)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [listScopeKey, currentUser, stores])

  // Keyed on user identity, not the object: profile edits (the Auto/Plan
  // switch saves the default) re-ran this and retired the open task's
  // own in-flight load, leaving its conversation empty.
  const currentUserId = currentUser?.id
  useEffect(() => {
    if (!currentUserId) { taskOpener.retire(); return } // signed out
    if (routeTaskId) {
      // Back on the open task: retire a load for a different task.
      if (selectedTask?.id !== routeTaskId) loadTaskById(routeTaskId)
      else taskOpener.retire(routeTaskId)
    } else {
      taskOpener.retire()
      if (selectedTask) { setSelectedTask(null); setSteps([]); setScreenshots({}); setLogs([]) }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [routeTaskId, currentUserId])

  useEffect(() => {
    if (!currentUser) return
    if (routeScheduleId) {
      if (selectedSchedule?.id !== routeScheduleId) {
        const s = schedules.find(x => x.id === routeScheduleId)
        if (s) loadSchedule(s)
      }
    } else if (selectedSchedule) {
      setSelectedSchedule(null); setScheduleTasks([])
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [routeScheduleId, currentUser, schedules])

  // ─── Data loaders ──────────────────────────────────
  const loadStores = async (): Promise<Store[]> => { const f: Store[] = await api.get('/api/stores'); setStores(f); return f }
  const loadProfiles = async () => { try { const data = await api.get('/api/profiles'); setProfiles(data.profiles || []) } catch { setProfiles([]) } }
  const loadUsers = async () => { try { setAllUsers(await api.get('/api/users')) } catch { /* ignore */ } }
  // The open schedule is refreshed from every reload too: plan events
  // (proposed / approved / re-planned) reload the list, and the detail
  // view + plan panel must not keep showing the state from selection.
  const loadSchedules = async () => {
    try {
      const fresh: Schedule[] = await api.get('/api/schedules')
      setSchedules(fresh)
      setSelectedSchedule(prev => (prev && fresh.find(x => x.id === prev.id)) || prev)
    } catch { setSchedules([]) }
  }
  // Loader (called by the route-param effect). The click handler
  // `selectSchedule` below only navigates; the URL change drives this.
  const loadSchedule = (schedule: Schedule) =>
    selectScheduleHandler(schedule, {
      api,
      inFlightScheduleIdRef,
      setSelectedSchedule,
      setSelectedTask,
      setScheduleTasks,
    })
  // Open a schedule under the scope whose list it was clicked in — the
  // URL records the browsed list, so the store is never dropped.
  const selectSchedule = (schedule: Schedule) =>
    navigate(scheduleTarget(routeStoreId, schedule.id))
  // Leave an open schedule detail (delete / mobile Back): to the SAME
  // scope's list, replacing history — Back must not return to it.
  const closeSchedule = () => { setSelectedSchedule(null); setScheduleTasks([]); navigate({ ...scheduleTarget(routeStoreId, null), replace: true }) }
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
    if (selectedSchedule?.id === id) closeSchedule() // leave a deleted detail
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
  const loadSyncMeta = async () => { try { const s = await api.get('/api/sync/status') as { jobs?: Array<Record<string, unknown>>; needs_restart?: boolean }; setWsSyncMeta({ ...(s.jobs?.find(j => j.name === 'knowledge') ?? {}), needs_restart: s.needs_restart }) } catch { /* ignore */ } }

  // ─── Store helpers ─────────────────────────────────
  // The selected backend's vendor, when it renders the generic picker
  // (a profile-listing vendor other than ziniao, which keeps its own).
  const genericVendor = vendors?.find(v => v.key === newStoreBackend
    && v.picker_kind === 'generic' && v.lists_profiles) ?? null
  const createStore = () => createStoreHandler({
    api, stores, newStoreName, newStoreBackend, genericVendor, selectedZiniaoAccountId,
    selectedBrowserOauth, newStoreProxyServer, newStoreProxyBypass, createStoreInFlight,
    setCreatingStore, setCreateStoreError, t, loadStores,
    onCreated: () => {
      setNewStoreName(''); setNewStoreBackend('chrome'); setSelectedZiniaoAccountId(''); setZiniaoBrowsers([]); setSelectedBrowserOauth(''); vendorBrowser.reset()
      setShowProxy(false); setNewStoreProxyServer(''); setNewStoreProxyBypass(''); setShowCreateStore(false)
    },
  })

  // Load a store's (or all-stores') task LIST. List-only — the open task
  // is owned by its route effect, so this must not touch it (raced with
  // loadTaskById). in-flight key = stale-response-wins guard.
  const selectScope = async (store: Store | null) => {
    setNavOpen(false)
    setSelectedStore(store); setShowAllTasks(!store)
    setTasks([]); setTasksLoading(true)
    const key = store ? store.id : '__none__'
    inFlightTasksKeyRef.current = key
    try {
      const data = await api.get(`/api/tasks?store_id=${key}`)
      if (inFlightTasksKeyRef.current === key) setTasks(data)
    } finally {
      if (inFlightTasksKeyRef.current === key) setTasksLoading(false)
    }
    loadSchedules()
  }
  // Click handlers navigate; the route-param effects call the loaders
  // (selectScope / loadTaskById) so URL is the single source of truth.
  // Sidebar clicks keep the sub-tab (listTarget): on 定时 they open
  // the store's / the all-stores SCHEDULE list.
  const selectStore = (store: Store) => navigate(listTarget(store.id, taskSubTab === 'scheduled'))
  const selectAllTasks = () => navigate(listTarget(null, taskSubTab === 'scheduled'))
  // A store task opens under its store (/stores/$storeId/tasks/$taskId) so
  // the URL keeps the store scope — otherwise the flat route drops it and
  // the sidebar snaps back to All Stores. No-store tasks use the flat form.
  const selectTask = (task: Task) =>
    task.store_id
      ? navigate({
          to: '/stores/$storeId/tasks/$taskId',
          params: { storeId: task.store_id, taskId: task.id },
        })
      : navigate({ to: '/tasks/$taskId', params: { taskId: task.id } })


  // Header actions of the open task; each rejects when it did not go
  // through (TasksView shows why), and a late response leaves another
  // task's state alone.
  const { stopAgent, retryTask, continueTask, deleteTask, isOpen } = useTaskActions({
    selectedTask, profileId: selectedProfileId, tasks, scheduleTasks,
    setTasks, setSelectedTask, setScheduleTasks,
    clearQuestions: () => { setPendingQuestions(null); setSelectedAnswers({}); setOtherInputs({}); setShowOtherInput({}) },
    clearDetail: () => { setSteps([]); setScreenshots({}); setAgentMessages([]); setTodoItems([]); setLogs([]); setConversationItems([]) },
  })
  const selectAnswer = (questionText: string, answer: string) => { setSelectedAnswers(prev => ({ ...prev, [questionText]: answer })); setShowOtherInput(prev => ({ ...prev, [questionText]: false })); setOtherInputs(prev => ({ ...prev, [questionText]: '' })) }
  const setAnswer = (questionText: string, answer: string) => { setSelectedAnswers(prev => { const n = { ...prev }; if (answer) n[questionText] = answer; else delete n[questionText]; return n }) }
  const toggleOtherInput = (questionText: string) => { setShowOtherInput(prev => { const show = !prev[questionText]; if (show) { setSelectedAnswers(p => { const n = { ...p }; delete n[questionText]; return n }) } else { setOtherInputs(p => ({ ...p, [questionText]: '' })); setSelectedAnswers(p => { const n = { ...p }; delete n[questionText]; return n }) } return { ...prev, [questionText]: show } }) }
  const setOtherAnswer = (questionText: string, text: string) => { setOtherInputs(prev => ({ ...prev, [questionText]: text })); if (text.trim()) { setSelectedAnswers(prev => ({ ...prev, [questionText]: text.trim() })) } else { setSelectedAnswers(prev => { const n = { ...prev }; delete n[questionText]; return n }) } }
  const submitAllAnswers = async (overrideAnswers?: Record<string, string>) => { if (!selectedTask || !pendingQuestions) return; const answers = overrideAnswers || selectedAnswers; const id = selectedTask.id; await api.post(`/api/tasks/${id}/questions/answer`, { request_id: pendingQuestions.request_id, answers }); if (!isOpen(id)) return; setPendingQuestions(null); setSelectedAnswers({}); setOtherInputs({}); setShowOtherInput({}) }
  const submitImageDecision = async (requestId: string, action: 'confirm' | 'cancel', prompt: string, model: string, addedReferences: string[] = []) => {
    if (!selectedTask) return
    // Optimistically mark the card resolved so its controls disable at
    // once; a confirm also gets the inline image via the image_generated
    // SSE event.
    setConversationItems(items => items.map(it =>
      it.type === 'image_request' && it.imageRequest?.requestId === requestId
        ? { ...it, imageRequest: { ...it.imageRequest!, resolved: true } }
        : it))
    try {
      await api.post(`/api/tasks/${selectedTask.id}/image/confirm`, { request_id: requestId, action, prompt, model, added_references: addedReferences })
    } catch { /* the tool call will time out server-side if unresolved */ }
  }
  const sendChatMessage = () =>
    sendChatMessageHandler({
      api, selectedTask, chatInput: chatDraft.get(), attachments: chatAttachments,
      profileId: selectedProfileId, conversationItems, sendingRef: sendingRefFor(selectedTask?.id ?? ''),
      setChatInput, setAttachments: setChatAttachments, setAgentMessages,
      setConversationItems, setSelectedTask, setTasks, isOpen,
    })

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

  // ─── Settings helpers ──────────────────────────────
  const createUser = async () => { if (!newUserForm.username.trim() || !newUserForm.password.trim()) return; try { await api.post('/api/users', { ...newUserForm, email: newUserForm.email.trim() || undefined }); setNewUserForm({ username: '', email: '', password: '', role: 'member' }); setShowAddUser(false); await loadUsers() } catch { /* ignore */ } }
  const deleteUser = async (userId: string, username: string) => { if (!confirm(t('settings.confirmDeleteUser', { username }))) return; try { await api.del(`/api/users/${userId}`); await loadUsers() } catch { /* ignore */ } }

  // ─── Side effects ──────────────────────────────────
  useEffect(() => { if (appView === 'workspace') { loadWsStructured(); loadSyncMeta() } else { stopWsAssistant() } }, [appView])
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { if (appView === 'settings') { loadEmailAccounts() } }, [appView])

  // SSE
  useSSE({
    enabled: authChecked && !!currentUser,
    selectedTaskId: selectedTask?.id, appView,
    selectedStoreId: selectedStore?.id ?? null,
    showAllTasks: showAllScope,
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
      else if (showAllScope) api.get('/api/tasks?store_id=__none__').then(setTasks).catch(() => {})
    },
    setWsAssistantMessages,
    setWsAssistantRunning,
    loadWsStructured,
  })

  const taskPanelActive = selectedStore || showAllScope
  const taskPanelTitle = showAllScope ? t('tasks.allStores') : selectedStore?.name || ''

  // ─── Login / loading ───────────────────────────────
  if (!authChecked) return <div className="flex items-center justify-center h-screen bg-gray-100 text-gray-500">{t('common.loading')}</div>
  if (!currentUser) return <LoginPage loginIdentifier={loginIdentifier} setLoginIdentifier={setLoginIdentifier} loginPassword={loginPassword} setLoginPassword={setLoginPassword} loginError={loginError} onLogin={handleLogin} />

  const submitCreateTask = async (title: string, description: string, files: PendingFile[], platform?: string, country?: string) => {
    await submitCreateTaskHandler(
      { title, description, files, platform, country },
      {
        api,
        storeId: selectedStore?.id || null,
        planMode: currentUser?.plan_mode_default ?? false,
        setTasks,
        setSelectedTask,
        uploadAttachment: (taskId, pf) => uploadTaskAttachment(taskId, pf.file),
        startTask: async (taskId) => { await api.post(`/api/tasks/${taskId}/start`, {}) },
        // A deferred task that never launches stays PENDING with no
        // error and emits no SSE — this alert is the only thing that
        // tells the user their task is not going to run.
        onStartError: (err) => alert(`${t('tasks.startFailed')}\n\n${err instanceof Error ? err.message : String(err)}`),
        onCreated: () => {
          setSteps([]); setScreenshots({}); setLogs([]); setConversationItems([]); setAgentMessages([]); setTodoItems([]); setPendingQuestions(null); setSelectedAnswers({}); setOtherInputs({}); setShowOtherInput({}); setChatInput(''); setChatAttachments([])
        },
      },
    )
  }

  return (
    <div className="flex flex-col h-dvh bg-gray-100 text-gray-900">
    {/* What waits for the user — ONE bar above every view, answers before approvals. */}
    <AttentionBanner
      schedules={schedules}
      onOpenTask={(w) => navigate(taskTarget(w.store_id, w.task_id))}
      onOpenQuestion={(q) => navigate(taskTarget(q.store_id, q.task_id))}
      onOpenApproval={(a) => navigate(scheduleTarget(a.store_id, a.schedule_id))}
      onOpenIntegrations={(key) => openSettings('integrations', `cli-${key}`)}
    />
    <div className="flex flex-1 min-h-0">
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
        stores={stores} selectedStore={selectedStore} showAllTasks={showAllScope}
        selectStore={selectStore} selectAllTasks={selectAllTasks}
        showCreateStore={showCreateStore} setShowCreateStore={setCreateStoreOpen} createStore={createStore}
        creatingStore={creatingStore} createStoreError={createStoreError} setCreateStoreError={setCreateStoreError}
        newStoreName={newStoreName} setNewStoreName={setNewStoreName}
        newStoreBackend={newStoreBackend} setNewStoreBackend={setNewStoreBackend}
        showProxy={showProxy} setShowProxy={setShowProxy}
        newStoreProxyServer={newStoreProxyServer} setNewStoreProxyServer={setNewStoreProxyServer}
        newStoreProxyBypass={newStoreProxyBypass} setNewStoreProxyBypass={setNewStoreProxyBypass}
        ziniaoAccounts={ziniao.ziniaoAccounts} selectedZiniaoAccountId={selectedZiniaoAccountId} setSelectedZiniaoAccountId={setSelectedZiniaoAccountId}
        ziniaoBrowsers={ziniao.ziniaoBrowsers} selectedBrowserOauth={selectedBrowserOauth} setSelectedBrowserOauth={setSelectedBrowserOauth}
        fetchingBrowsers={ziniao.fetchingBrowsers} browserFetchError={ziniao.browserFetchError} setBrowserFetchError={ziniao.setBrowserFetchError}
        fetchBrowserProfiles={ziniao.fetchBrowserProfiles} ziniaoStartingSecs={ziniao.ziniaoStartingSecs}
        vendors={vendors}
        vendorStores={vendorBrowser.stores} vendorError={vendorBrowser.error}
        vendorErrorMessage={vendorBrowser.errorMessage}
        fetchingVendorStores={vendorBrowser.fetching} fetchVendorStores={vendorBrowser.fetchStores}
        resetVendorStores={vendorBrowser.reset}
        serverPlatform={serverPlatform} serverVersion={serverVersion} installId={installId}
        updateStatus={updateFlow.status} onOpenUpdate={updateFlow.openModal}
        showAddAccount={ziniao.showAddAccount} setShowAddAccount={ziniao.setShowAddAccount}
        showAccountPassword={ziniao.showAccountPassword} setShowAccountPassword={ziniao.setShowAccountPassword}
        editingAccountId={ziniao.editingAccountId} setEditingAccountId={ziniao.setEditingAccountId}
        newAccount={ziniao.newAccount} setNewAccount={ziniao.setNewAccount}
        createZiniaoAccount={ziniao.createZiniaoAccount} updateZiniaoAccount={ziniao.updateZiniaoAccount} deleteZiniaoAccount={ziniao.deleteZiniaoAccount}
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
          onOpenSettings={openSettings}
          isMobile={isMobile} onOpenNav={() => setNavOpen(true)}
          taskPanelActive={!!taskPanelActive} taskPanelTitle={taskPanelTitle}
          tasks={tasks} tasksLoading={tasksLoading} selectedTask={selectedTask} steps={steps}
          taskLoad={taskOpener.taskLoad} onRetryTaskLoad={taskOpener.retry}
          onLeaveTaskLoad={() => (routeStoreId ? navigate({ to: '/stores/$storeId', params: { storeId: routeStoreId } }) : navigate({ to: '/tasks' }))} screenshots={screenshots} logs={logs}
          agentMessages={agentMessages} todoItems={todoItems} pendingQuestions={pendingQuestions}
          conversationItems={conversationItems}
          selectedAnswers={selectedAnswers} otherInputs={otherInputs} showOtherInput={showOtherInput}
          chatDraft={chatDraft}
          chatAttachments={chatAttachments} setChatAttachments={setChatAttachments} debugMode={debugMode} setDebugMode={setDebugMode}
          profiles={profiles} selectedProfileId={selectedProfileId} setSelectedProfileId={setSelectedProfileId}
          currentUser={currentUser} showAllTasks={showAllScope}
          openCreateModal={() => aiGate.guardOpen(() => setShowCreateTask(true))} selectTask={selectTask}
          stopAgent={stopAgent} retryTask={retryTask} continueTask={continueTask} deleteTask={deleteTask}
          selectAnswer={selectAnswer} setAnswer={setAnswer} toggleOtherInput={toggleOtherInput}
          setOtherAnswer={setOtherAnswer} submitAllAnswers={submitAllAnswers} submitImageDecision={submitImageDecision} sendChatMessage={sendChatMessage}
          setSelectedTask={setSelectedTask} setTasks={setTasks}
          setEditingProfile={setEditingProfile} setShowProfileModal={setShowProfileModal}
          questionBannerRef={questionBannerRef}
          taskSubTab={taskSubTab} setTaskSubTab={setTaskSubTab}
          schedules={schedules} selectedSchedule={selectedSchedule} scheduleTasks={scheduleTasks}
          showCreateSchedule={showCreateSchedule} setShowCreateSchedule={v => (v ? aiGate.guardOpen(() => setShowCreateSchedule(true)) : setShowCreateSchedule(false))}
          selectSchedule={selectSchedule} deleteSchedule={deleteSchedule}
          toggleSchedulePause={toggleSchedulePause} triggerSchedule={triggerSchedule}
          replanSchedule={replanSchedule}
          setSelectedSchedule={setSelectedSchedule} closeSchedule={closeSchedule}
          onScheduleUpdated={onScheduleUpdated}
          selectedStore={selectedStore}
          stores={stores}
        />
      ) : appView === 'workspace' ? (
        <div className="flex-1 flex flex-col min-w-0">
          {/* Mode toggle header */}
          <div className="px-4 py-2.5 bg-white border-b border-gray-100 flex items-center justify-end gap-2">
            {isMobile && (
              <MobileMenuButton onClick={() => setNavOpen(true)} label={t('common.menu', 'Menu')} className="mr-auto -ml-1" />
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
            <MobileMenuButton onClick={() => setNavOpen(true)} label={t('common.menu', 'Menu')} className="-ml-1" />
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
      {showCreateTask && <CreateTaskModal showAllTasks={showAllTasks} storeName={selectedStore?.name} selectedStore={selectedStore} onClose={() => setShowCreateTask(false)} onSubmit={submitCreateTask} />}
      {aiGate.prompt}
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
          onSave={async (profile, opts) => {
            if (editingProfile) { await updateProfile(editingProfile.id, profile); return }
            // Create errors (e.g. a 400) propagate so the
            // modal can surface them and stay open.
            const newProfile = await createProfile(profile)
            if (opts.setAsDefault) {
              // Best-effort: the profile is already created, so a
              // set-default hiccup must not lose it or trap the modal.
              try {
                await api.patch(`/api/profiles/${newProfile.id}/set-default`)
                setCurrentUser(prev => prev ? { ...prev, default_profile_id: newProfile.id } : prev)
                setSelectedProfileId(newProfile.id)
              } catch (err) { console.error('set-default after create failed', err) }
            } else if (profiles.length === 0) {
              setSelectedProfileId(newProfile.id)
            }
          }}
          editingProfile={editingProfile}
          isAdmin={currentUser?.role === 'admin'}
        />
      )}
      <UpdateFlow flow={updateFlow} isAdmin={currentUser?.role === 'admin'} />
    </div>
    </div>
  )
}
