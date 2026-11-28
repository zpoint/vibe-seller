import { useEffect, useRef, useState } from 'react'

import { advanceAuthGeneration, AUTH_EXPIRED_EVENT } from '../api'
import { registerUserHash, unregisterUserHash } from '../lib/telemetry'
import type {
  AgentMessage, AuthUser, ConversationItem, EmailAccount, Profile,
  Schedule, Store, StagedAttachment, Task, TaskStep, TodoItem, WsStructured,
} from '../types'

interface PendingQuestions {
  request_id: string
  questions: {
    header?: string
    question: string
    options?: { label: string; description?: string }[]
  }[]
}

interface SessionSetterValues {
  navOpen: boolean
  stores: Store[]
  selectedStore: Store | null
  showAllTasks: boolean
  tasks: Task[]
  tasksLoading: boolean
  selectedTask: Task | null
  steps: TaskStep[]
  screenshots: Record<string, string>
  logs: string[]
  newStoreName: string
  newStoreBackend: string
  showCreateStore: boolean
  creatingStore: boolean
  createStoreError: string
  showCreateTask: boolean
  agentMessages: AgentMessage[]
  todoItems: TodoItem[]
  pendingQuestions: PendingQuestions | null
  selectedAnswers: Record<string, string>
  otherInputs: Record<string, string>
  showOtherInput: Record<string, boolean>
  chatAttachments: StagedAttachment[]
  conversationItems: ConversationItem[]
  debugMode: boolean
  profiles: Profile[]
  selectedProfileId: string
  showProfileModal: boolean
  editingProfile: Profile | undefined
  wsAssistantActive: boolean
  wsAssistantMessages: AgentMessage[]
  wsAssistantRunning: boolean
  wsStructured: WsStructured | null
  wsSelectedFile: string | null
  wsFileContent: string
  wsEditorContent: string
  wsEditorDirty: boolean
  wsSaving: boolean
  wsExpandedStores: Set<string>
  wsExpandedSkills: Set<string>
  wsNewFileName: string
  wsNewFileSection: string | null
  wsSyncMeta: Record<string, unknown> | null
  wsSyncing: boolean
  wsSkillsSyncing: boolean
  wsFileHistory: Array<{ sha: string; message: string; date: string; author: string }>
  wsShowHistory: boolean
  wsPreviewCommit: string | null
  wsPreviewContent: string
  showProxy: boolean
  newStoreProxyServer: string
  newStoreProxyBypass: string
  schedules: Schedule[]
  selectedSchedule: Schedule | null
  scheduleTasks: Task[]
  showCreateSchedule: boolean
  emailAccounts: EmailAccount[]
  allUsers: AuthUser[]
  showAddUser: boolean
  newUserForm: { username: string; email: string; password: string; role: string }
}

type SessionSetters = {
  [K in keyof SessionSetterValues]: (value: SessionSetterValues[K]) => void
}

interface SessionBoundaryDeps {
  t: (key: string) => string
  navigate: (options: { to: string; replace: boolean }) => void
  taskOpener: { retire: () => void }
  ziniao: { reset: () => void }
  vendorBrowser: { reset: () => void }
  chatDraft: { reset: () => void }
  inFlightTasksKeyRef: React.MutableRefObject<string | null>
  inFlightScheduleIdRef: React.MutableRefObject<string | null>
  sendingRefs: React.MutableRefObject<Map<string, { current: boolean }>>
  createStoreInFlight: React.MutableRefObject<boolean>
  setters: SessionSetters
}

/** Owns authentication and the one synchronous boundary that clears A before B. */
export function useSessionBoundary(d: SessionBoundaryDeps) {
  const [currentUser, setCurrentUser] = useState<AuthUser | null>(null)
  const currentUserRef = useRef<AuthUser | null>(null)
  currentUserRef.current = currentUser
  const [authChecked, setAuthChecked] = useState(false)
  const [authRequired, setAuthRequired] = useState(false)
  const [loginIdentifier, setLoginIdentifier] = useState('')
  const [loginPassword, setLoginPassword] = useState('')
  const [loginError, setLoginError] = useState('')
  const loginInFlight = useRef(false)
  const s = d.setters

  // Nothing here runs for navigation, same-user object updates, or forms.
  const resetSessionState = () => {
    advanceAuthGeneration()
    d.taskOpener.retire()
    d.ziniao.reset()
    d.vendorBrowser.reset()
    d.inFlightTasksKeyRef.current = null
    d.inFlightScheduleIdRef.current = null
    d.sendingRefs.current.clear()
    d.createStoreInFlight.current = false
    d.chatDraft.reset()
    setLoginIdentifier(''); setLoginPassword(''); setLoginError('')
    s.navOpen(false)
    s.stores([]); s.selectedStore(null); s.showAllTasks(true)
    s.tasks([]); s.tasksLoading(false); s.selectedTask(null)
    s.steps([]); s.screenshots({}); s.logs([])
    s.newStoreName(''); s.newStoreBackend('chrome'); s.showCreateStore(false)
    s.creatingStore(false); s.createStoreError('')
    s.showCreateTask(false); s.agentMessages([]); s.todoItems([])
    s.pendingQuestions(null); s.selectedAnswers({}); s.otherInputs({}); s.showOtherInput({})
    s.chatAttachments([]); s.conversationItems([]); s.debugMode(false)
    s.profiles([]); s.selectedProfileId('default'); s.showProfileModal(false); s.editingProfile(undefined)
    s.wsAssistantActive(false); s.wsAssistantMessages([]); s.wsAssistantRunning(false)
    s.wsStructured(null); s.wsSelectedFile(null); s.wsFileContent(''); s.wsEditorContent(''); s.wsEditorDirty(false)
    s.wsSaving(false); s.wsExpandedStores(new Set()); s.wsExpandedSkills(new Set())
    s.wsNewFileName(''); s.wsNewFileSection(null); s.wsSyncMeta(null); s.wsSyncing(false); s.wsSkillsSyncing(false)
    s.wsFileHistory([]); s.wsShowHistory(false); s.wsPreviewCommit(null); s.wsPreviewContent('')
    s.showProxy(false); s.newStoreProxyServer(''); s.newStoreProxyBypass('')
    s.schedules([]); s.selectedSchedule(null); s.scheduleTasks([]); s.showCreateSchedule(false)
    s.emailAccounts([]); s.allUsers([]); s.showAddUser(false)
    s.newUserForm({ username: '', email: '', password: '', role: 'member' })
  }
  const endSession = () => {
    resetSessionState()
    unregisterUserHash()
    currentUserRef.current = null
    setCurrentUser(null)
    d.navigate({ to: '/', replace: true })
  }
  const applyAuthenticatedUser = (u: AuthUser) => {
    const previous = currentUserRef.current
    if (previous?.id !== u.id) {
      if (previous) resetSessionState()
      advanceAuthGeneration()
    }
    currentUserRef.current = u
    setCurrentUser(u)
    void registerUserHash(u.id)
    if (u.default_profile_id) s.selectedProfileId(u.default_profile_id)
    s.debugMode(u.debug_mode ?? false)
  }
  const handleLogin = async () => {
    // Synchronous guard closes Enter + click before React can rerender.
    if (loginInFlight.current) return
    loginInFlight.current = true
    setLoginError('')
    try {
      const r = await fetch('/api/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ identifier: loginIdentifier, password: loginPassword }), credentials: 'include' })
      if (!r.ok) { setLoginError(d.t('auth.invalidCredentials')); return }
      const u = await r.json()
      applyAuthenticatedUser(u)
      setLoginIdentifier(''); setLoginPassword('')
    } catch {
      setLoginError(d.t('auth.invalidCredentials'))
    } finally {
      loginInFlight.current = false
    }
  }
  // B cannot start login until the old cookie-clearing response has settled.
  const handleLogout = async () => {
    try { await fetch('/api/auth/logout', { method: 'POST', credentials: 'include' }) } catch { /* local logout still proceeds */ }
    endSession()
  }

  useEffect(() => {
    const onExpired = () => {
      // Anonymous registry probes can 401; only a real live session expires.set
      if (currentUserRef.current) endSession()
    }
    window.addEventListener(AUTH_EXPIRED_EVENT, onExpired)
    return () => window.removeEventListener(AUTH_EXPIRED_EVENT, onExpired)
  // The listener owns the session boundary; identity lives in currentUserRef.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    fetch('/api/auth/status')
      .then(r => r.json())
      .then(({ auth_required }) => {
        setAuthRequired(auth_required)
        return fetch('/api/auth/me', { credentials: 'include' })
          .then(r => { if (!r.ok) throw new Error(); return r.json() })
          .then(u => { applyAuthenticatedUser(u); setAuthChecked(true) })
          .catch(() => { setCurrentUser(null); setAuthChecked(true) })
      })
      .catch(() => { setCurrentUser(null); setAuthChecked(true) })
  // Initial authentication only; later logins call applyAuthenticatedUser.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return {
    currentUser, setCurrentUser, authChecked, authRequired, setAuthRequired,
    loginIdentifier, setLoginIdentifier, loginPassword, setLoginPassword,
    loginError, handleLogin, handleLogout,
  }
}
