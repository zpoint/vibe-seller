import { useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { api } from '../api'
import { sendEvent, ageBucket } from '../lib/telemetry'
import { FrontendEvent } from '../lib/telemetryEvents'
import { StatusBadge } from '../components/ui'
import { ConversationStream } from '../components/conversation/ConversationStream'
import { ScheduleList } from '../components/ScheduleList'
import { ScheduleDetailView } from '../components/ScheduleDetailView'
import { EditScheduleModal } from '../components/EditScheduleModal'
import { ExternalConfigOverrideErrorCard } from '../components/ExternalConfigOverrideErrorCard'
import { getUI, hasProgressingTask } from '../taskStates'
import type { Task, TaskStep, AgentMessage, TodoItem, AuthUser, Profile, Schedule, Store, ConversationItem } from '../types'

function formatDate(dateStr: string): string {
  const d = new Date(dateStr)
  if (isNaN(d.getTime())) return '--'
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}.${pad(d.getMonth() + 1)}.${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

interface TasksViewProps {
  taskPanelActive: boolean
  taskPanelTitle: string
  tasks: Task[]
  selectedTask: Task | null
  steps: TaskStep[]
  screenshots: Record<string, string>
  logs: string[]
  agentMessages: AgentMessage[]
  todoItems: TodoItem[]
  pendingQuestions: { request_id: string; questions: { header?: string; question: string; options?: { label: string; description?: string }[] }[] } | null
  conversationItems: ConversationItem[]
  selectedAnswers: Record<string, string>
  otherInputs: Record<string, string>
  showOtherInput: Record<string, boolean>
  chatInput: string
  setChatInput: (v: string) => void
  debugMode: boolean
  setDebugMode: React.Dispatch<React.SetStateAction<boolean>>
  profiles: Profile[]
  selectedProfileId: string
  setSelectedProfileId: (id: string) => void
  currentUser: AuthUser
  showAllTasks: boolean
  // Callbacks
  openCreateModal: () => void
  selectTask: (task: Task) => void
  stopAgent: () => void
  retryTask: (taskId: string) => void
  deleteTask: (taskId: string) => void
  selectAnswer: (questionText: string, answer: string) => void
  toggleOtherInput: (questionText: string) => void
  setOtherAnswer: (questionText: string, text: string) => void
  submitAllAnswers: (overrideAnswers?: Record<string, string>) => void
  sendChatMessage: () => void
  setSelectedTask: React.Dispatch<React.SetStateAction<Task | null>>
  setTasks: React.Dispatch<React.SetStateAction<Task[]>>
  setCurrentUser: React.Dispatch<React.SetStateAction<AuthUser | null>>
  setEditingProfile: (p: Profile | undefined) => void
  setShowProfileModal: (v: boolean) => void
  questionBannerRef: React.RefObject<HTMLDivElement | null>
  // Schedule props
  taskSubTab: 'onetime' | 'scheduled'
  setTaskSubTab: (tab: 'onetime' | 'scheduled') => void
  schedules: Schedule[]
  selectedSchedule: Schedule | null
  scheduleTasks: Task[]
  showCreateSchedule: boolean
  setShowCreateSchedule: (v: boolean) => void
  selectSchedule: (schedule: Schedule) => void
  deleteSchedule: (id: string) => void
  toggleSchedulePause: (id: string, isActive: boolean) => void
  triggerSchedule: (id: string) => void
  replanSchedule: (id: string) => void
  setSelectedSchedule: React.Dispatch<React.SetStateAction<Schedule | null>>
  onScheduleUpdated: (schedule: Schedule) => void
  selectedStore: Store | null
  stores: Store[]
}

function SubtaskList({ parentTaskId, onSelect, stores }: {
  parentTaskId: string
  onSelect: (t: Task) => void
  stores: Store[]
}) {
  const { t } = useTranslation()
  const [children, setChildren] = useState<Task[]>([])

  useEffect(() => {
    let cancelled = false
    const fetchChildren = () => {
      api.get(`/api/tasks?parent_task_id=${encodeURIComponent(parentTaskId)}`).then((data: Task[]) => {
        if (!cancelled) setChildren(data)
      }).catch(() => {})
    }
    fetchChildren()
    // Poll every 10s for status updates instead of opening a second SSE
    const interval = setInterval(fetchChildren, 10000)
    return () => { cancelled = true; clearInterval(interval) }
  }, [parentTaskId])

  if (children.length === 0) return null

  const storeName = (sid: string | null) => {
    if (!sid) return ''
    return stores.find(s => s.id === sid)?.name || sid.slice(0, 8)
  }

  return (
    <div className="mt-2">
      <div className="text-xs font-medium text-gray-500 mb-1">{t('tasks.subtasks', 'Subtasks')} ({children.length})</div>
      <div className="space-y-1">
        {children.map(child => (
          <button
            key={child.id}
            onClick={() => onSelect(child)}
            className="w-full text-left px-2 py-1.5 text-xs rounded border border-gray-200 hover:bg-gray-50 flex items-center gap-2"
          >
            <StatusBadge status={child.status} />
            <span className="truncate flex-1">{child.title}</span>
            {child.store_id && <span className="text-gray-400 text-[10px]">{storeName(child.store_id)}</span>}
          </button>
        ))}
      </div>
    </div>
  )
}

export function TasksView({
  taskPanelActive,
  taskPanelTitle,
  tasks,
  selectedTask,
  steps,
  screenshots,
  logs,
  agentMessages,
  todoItems,
  pendingQuestions,
  conversationItems,
  selectedAnswers,
  otherInputs,
  showOtherInput,
  chatInput,
  setChatInput,
  debugMode,
  setDebugMode,
  profiles,
  selectedProfileId,
  setSelectedProfileId,
  currentUser,
  showAllTasks,
  openCreateModal,
  selectTask,
  stopAgent,
  retryTask,
  deleteTask,
  selectAnswer,
  toggleOtherInput,
  setOtherAnswer,
  submitAllAnswers,
  sendChatMessage,
  setSelectedTask,
  setTasks,
  setCurrentUser,
  setEditingProfile,
  setShowProfileModal,
  questionBannerRef,
  taskSubTab,
  setTaskSubTab,
  schedules,
  selectedSchedule,
  scheduleTasks,
  setShowCreateSchedule,
  selectSchedule,
  deleteSchedule,
  toggleSchedulePause,
  triggerSchedule,
  replanSchedule,
  setSelectedSchedule,
  onScheduleUpdated,
  selectedStore,
  stores,
}: TasksViewProps) {
  const { t } = useTranslation()
  // Gating predicate for the schedule "Run Now" button: true
  // when ANY status is still progressing (pending / queued /
  // designing / planned / running — NOT just `running`). See
  // `taskStates.ts::PROGRESSING_STATUSES`; backend mirror is
  // `tests/unit/test_task_states.py`.
  const hasProgressingScheduleTask = useMemo(
    () => hasProgressingTask(scheduleTasks),
    [scheduleTasks],
  )
  const logsEndRef = useRef<HTMLDivElement>(null)
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const [debugCopied, setDebugCopied] = useState(false)
  const [descExpanded, setDescExpanded] = useState(false)
  const [editingSchedule, setEditingSchedule] = useState<Schedule | null>(null)
  const prevTaskIdRef = useRef(selectedTask?.id)
  if (prevTaskIdRef.current !== selectedTask?.id) {
    prevTaskIdRef.current = selectedTask?.id
    if (descExpanded) setDescExpanded(false)
  }
  const debugCopyTimer = useRef<number>(0)
  useEffect(() => () => window.clearTimeout(debugCopyTimer.current), [])
  const scrollContainerRef = useRef<HTMLDivElement>(null)
  const userNearBottom = useRef(true)

  // Auto-grow chat textarea up to ~8 lines, then scroll
  const chatInputRef = useRef<HTMLTextAreaElement>(null)
  useEffect(() => {
    const el = chatInputRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 192)}px`
  }, [chatInput, selectedTask?.id])

  // Phase helpers — driven by centralized UI config
  const taskCfg = selectedTask ? getUI(selectedTask.status) : null

  // Unified send bar state
  const canSend = taskCfg?.canSendMessage ?? false
  const isActive = taskCfg?.isActive ?? false
  const hasText = chatInput.trim().length > 0

  // Placeholder changes by state
  const getPlaceholder = () => {
    if (!selectedTask) return t('tasks.chatPlaceholder')
    if (selectedTask.status === 'planned') return t('tasks.planFeedbackPlaceholder')
    if (selectedTask.status === 'waiting') return t('tasks.wakeMessagePlaceholder')
    if (selectedTask.status === 'completed' || selectedTask.status === 'failed') return t('tasks.followupPlaceholder')
    return t('tasks.chatPlaceholder')
  }

  const handleConfirmPlan = async () => {
    if (!selectedTask) return
    try {
      await api.post(`/api/tasks/${selectedTask.id}/execute-plan`)
      setSelectedTask(prev => prev ? { ...prev, status: 'running' } : prev)
      setTasks(prev => prev.map(t2 => t2.id === selectedTask.id ? { ...t2, status: 'running' } : t2))
    } catch { /* ignore */ }
  }

  const DAYS = [t('schedules.mon'), t('schedules.tue'), t('schedules.wed'), t('schedules.thu'), t('schedules.fri'), t('schedules.sat'), t('schedules.sun')]
  const getScheduleBadge = (s: Schedule) => {
    const time = s.schedule_time?.slice(0, 5) || '00:00'
    const n = s.interval_value || 1
    if (s.schedule_type === 'minutes') return `${t('schedules.every')} ${n} ${t('schedules.minutes')}`
    if (s.schedule_type === 'hours') return `${t('schedules.every')} ${n} ${t('schedules.hours')}`
    if (s.schedule_type === 'days') return n === 1 ? `${t('schedules.daily')} ${time}` : `${t('schedules.every')} ${n} ${t('schedules.days')} ${time}`
    if (s.schedule_type === 'weekly') return `${t('schedules.weekly')} ${DAYS[(s.schedule_day || 1) - 1]} ${time}`
    if (s.schedule_type === 'monthly') return `${t('schedules.monthly')} ${s.schedule_day || 1} ${time}`
    if (s.schedule_type === 'daily') return `${t('schedules.daily')} ${time}`
    return `${s.schedule_type} ${time}`
  }

  const onetimeTasks = tasks.filter(task => !task.schedule_id)

  return (
    <>
      {/* Middle: Task list */}
      <div className="w-80 bg-white border-r border-gray-200 flex flex-col">
        {taskPanelActive ? (
          <>
            <div className="p-4 pb-0 border-b border-gray-200">
              <div className="flex items-center justify-between mb-2">
                <div>
                  <h2 className="font-semibold">{taskPanelTitle}</h2>
                </div>
                <button
                  onClick={taskSubTab === 'onetime' ? openCreateModal : () => setShowCreateSchedule(true)}
                  className="px-3 py-1.5 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 flex items-center gap-1"
                >
                  <span className="text-base leading-none">+</span> {taskSubTab === 'onetime' ? t('tasks.newTask') : t('schedules.newSchedule')}
                </button>
              </div>
              <div className="flex bg-gray-100 rounded-lg p-0.5 mb-2">
                <button
                  onClick={() => { setTaskSubTab('onetime'); setSelectedSchedule(null) }}
                  className={`flex-1 px-2 py-1 text-xs font-medium rounded-md transition-colors ${taskSubTab === 'onetime' ? 'bg-white shadow text-gray-900' : 'text-gray-500 hover:text-gray-700'}`}
                >
                  {t('schedules.oneTimeTasks')}
                </button>
                <button
                  onClick={() => setTaskSubTab('scheduled')}
                  className={`flex-1 px-2 py-1 text-xs font-medium rounded-md transition-colors ${taskSubTab === 'scheduled' ? 'bg-white shadow text-gray-900' : 'text-gray-500 hover:text-gray-700'}`}
                >
                  {t('schedules.scheduledTasks')}
                </button>
              </div>
            </div>
            <div className="flex-1 overflow-y-auto">
              {taskSubTab === 'onetime' ? (
                <>
                  {onetimeTasks.map(task => (
                    <button
                      key={task.id}
                      onClick={() => {
                        sendEvent(FrontendEvent.TASK_OPENED, {
                          task_status: task.status,
                          is_planned: !!task.plan_mode,
                          has_error: !!task.error,
                          age_bucket: ageBucket(task.created_at),
                        })
                        selectTask(task); setSelectedSchedule(null)
                      }}
                      className={`w-full text-left px-4 py-3 hover:bg-gray-50 border-b border-gray-100 ${selectedTask?.id === task.id && !selectedSchedule ? 'bg-blue-50' : ''} ${task.status === 'waiting' ? 'border-l-3 border-l-amber-400' : ''}`}
                    >
                      <div className="font-medium text-sm truncate">{task.title}</div>
                      {task.description && <div className="text-xs text-gray-400 truncate">{task.description}</div>}
                      <div className="flex items-center gap-2 mt-1 min-w-0">
                        <StatusBadge status={task.status} />
                        {task.status === 'waiting' && task.wait_condition && (() => {
                          try {
                            const cond = JSON.parse(task.wait_condition)
                            const strategy = cond.check_strategy || 'manual'
                            return (
                              <span className={`text-[10px] px-1 py-0.5 rounded whitespace-nowrap shrink-0 ${strategy === 'email' ? 'bg-blue-50 text-blue-600' : 'bg-gray-100 text-gray-500'}`}>
                                {strategy === 'email' ? t('waiting.autoEmail') : t('waiting.manual')}
                              </span>
                            )
                          } catch { return null }
                        })()}
                        <span className="text-xs text-gray-400 truncate min-w-0 flex-1">{task.created_by_name || 'admin'}</span>
                        <span className="text-xs text-gray-400 whitespace-nowrap shrink-0">{formatDate(task.created_at)}</span>
                      </div>
                    </button>
                  ))}
                  {onetimeTasks.length === 0 && (
                    <div className="flex flex-col items-center justify-center py-12 px-4 text-center">
                      <div className="text-gray-300 text-4xl mb-3">&#9998;</div>
                      <p className="text-sm text-gray-500 mb-3">{t('tasks.noTasks')}</p>
                      <button
                        onClick={openCreateModal}
                        className="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700"
                      >
                        + {t('tasks.createFirstTask')}
                      </button>
                    </div>
                  )}
                </>
              ) : (
                /* ── Scheduled tab: store-specific + all-stores schedules ── */
                <ScheduleList
                  schedules={schedules}
                  selectedSchedule={selectedSchedule}
                  showAllTasks={showAllTasks}
                  selectedStore={selectedStore}
                  stores={stores}
                  selectSchedule={selectSchedule}
                  setShowCreateSchedule={setShowCreateSchedule}
                  getScheduleBadge={getScheduleBadge}
                />
              )}
            </div>
          </>
        ) : (
          <div className="flex items-center justify-center h-full text-gray-400 text-sm">
            {t('tasks.noStoreSelected')}
          </div>
        )}
      </div>

      {/* Right: Task detail or Schedule detail */}
      <div className="flex-1 flex flex-col bg-gray-50">
        {selectedSchedule && !selectedTask ? (
          <ScheduleDetailView
            schedule={selectedSchedule}
            scheduleTasks={scheduleTasks}
            stores={stores}
            selectedStore={selectedStore}
            showAllTasks={showAllTasks}
            hasProgressingScheduleTask={hasProgressingScheduleTask}
            selectTask={selectTask}
            toggleSchedulePause={toggleSchedulePause}
            triggerSchedule={triggerSchedule}
            replanSchedule={replanSchedule}
            setEditingSchedule={setEditingSchedule}
            deleteSchedule={deleteSchedule}
            getScheduleBadge={getScheduleBadge}
            formatDate={formatDate}
          />
        ) : selectedTask ? (
          <>
            {/* Fixed header */}
            <div className="bg-white border-b border-gray-200">
              <div className="p-4 pb-0">
                {selectedSchedule && (
                  <button
                    onClick={() => { setSelectedTask(null) }}
                    className="text-xs text-blue-600 hover:text-blue-800 mb-2 flex items-center gap-1"
                  >
                    <span>&larr;</span> {t('schedules.backToSchedule')}: {selectedSchedule.title}
                  </button>
                )}
                <div className="flex items-center gap-3">
                  <StatusBadge status={selectedTask.status} />
                  {selectedTask.description ? (
                    <button
                      type="button"
                      aria-expanded={descExpanded}
                      className="flex items-center gap-1.5 hover:text-indigo-600"
                      onClick={() => setDescExpanded(e => !e)}
                    >
                      <span className={`text-[10px] text-gray-400 transition-transform ${descExpanded ? 'rotate-90' : ''}`}>▶</span>
                      <h2 className="font-semibold">{selectedTask.title}</h2>
                    </button>
                  ) : (
                    <h2 className="font-semibold">{selectedTask.title}</h2>
                  )}
                  {selectedTask.store_id && (
                    <span className="text-xs text-gray-500 bg-gray-100 px-1.5 py-0.5 rounded">{stores.find(s => s.id === selectedTask.store_id)?.name || selectedTask.store_id.slice(0, 8)}</span>
                  )}
                  <span className="text-xs text-gray-400">{selectedTask.created_by_name || 'admin'}</span>
                </div>
                {selectedTask.description && descExpanded && (
                  <p className="text-sm text-gray-500 mt-1">{selectedTask.description}</p>
                )}
                {/* Plan progress / view plan shortcut */}
                {todoItems.length > 0 ? (
                  <button
                    onClick={() => document.querySelector('[data-plan-card]')?.scrollIntoView({ behavior: 'smooth' })}
                    className="flex items-center gap-1.5 text-xs text-gray-500 hover:text-indigo-600 mt-1"
                  >
                    <span>{t('tasks.stepProgress', { completed: todoItems.filter(ti => ti.status === 'completed').length, total: todoItems.length })}</span>
                    <div className="flex gap-0.5">
                      {todoItems.map((ti, i) => (
                        <div key={i} className={`w-1.5 h-1.5 rounded-full ${
                          ti.status === 'completed' ? 'bg-green-500' :
                          ti.status === 'in_progress' ? 'bg-blue-500 animate-pulse' :
                          'bg-gray-300'
                        }`} />
                      ))}
                    </div>
                  </button>
                ) : selectedTask.plan ? (
                  <button
                    onClick={() => document.querySelector('[data-plan-card]')?.scrollIntoView({ behavior: 'smooth' })}
                    className="text-xs text-indigo-500 hover:text-indigo-700 mt-1"
                  >
                    {t('tasks.viewPlan', 'View Plan')}
                  </button>
                ) : null}
                {!selectedTask.store_id && selectedTask.status === 'pending' && (
                  <div className="mt-2 text-sm text-amber-600 bg-amber-50 px-3 py-2 rounded">
                    {t('tasks.noStoreWarning')}
                  </div>
                )}
                {selectedTask.error && (
                  selectedTask.error_category === 'external_config_override'
                    ? <ExternalConfigOverrideErrorCard error={selectedTask.error} />
                    : (
                  <div className="mt-2 text-sm text-red-600 bg-red-50 px-3 py-2 rounded whitespace-pre-line">
                    {selectedTask.error_category
                      ? t(`tasks.error_${selectedTask.error_category}`, selectedTask.error)
                      : selectedTask.error}
                    {selectedTask.error.includes('/api/ziniao/launcher') && (
                      <a href="/api/ziniao/launcher" download className="ml-2 inline-block px-2 py-0.5 bg-blue-600 text-white rounded text-xs hover:bg-blue-700">{t('settings.ziniaoDownloadLauncher')}</a>
                    )}
                  </div>
                    )
                )}
                <SubtaskList parentTaskId={selectedTask.id} onSelect={selectTask} stores={stores} />
                <div className="mt-2 flex gap-2 flex-wrap">
                  {getUI(selectedTask.status).isActive && (
                    <span className="text-xs text-blue-600 animate-pulse self-center">
                      {selectedTask.status === 'designing' ? t('tasks.designing') : t('tasks.running')}
                    </span>
                  )}
                  {getUI(selectedTask.status).canStopHeader && (
                    <button onClick={stopAgent} className="px-3 py-1 text-xs bg-red-600 text-white rounded hover:bg-red-700">
                      {t('tasks.stopTask')}
                    </button>
                  )}
                  {getUI(selectedTask.status).canRetry && (
                    <button onClick={() => retryTask(selectedTask.id)} className="px-3 py-1 text-xs text-gray-600 border border-gray-300 rounded hover:bg-gray-100">
                      {selectedTask.status === 'completed' ? t('tasks.rerun') : t('tasks.retry')}
                    </button>
                  )}
                  {!getUI(selectedTask.status).isActive && !selectedTask.is_plan_only && (
                    <button
                      onClick={() => deleteTask(selectedTask.id)}
                      className="px-3 py-1 text-xs text-red-600 border border-red-300 rounded hover:bg-red-50"
                    >
                      {t('common.delete')}
                    </button>
                  )}
                </div>
              </div>
              <div className="border-b border-gray-200 mt-3" />
            </div>

            {/* Scrollable conversation stream */}
            <div
              ref={scrollContainerRef}
              onScroll={() => {
                const el = scrollContainerRef.current
                if (el) userNearBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 100
              }}
              className="flex-1 overflow-y-auto p-4"
            >
              {debugMode ? (
                /* Debug mode: raw messages */
                <div className="mb-4">
                  {agentMessages.length > 0 ? (
                    <div className="overflow-y-auto overflow-x-hidden bg-gray-900 rounded-lg border border-gray-700">
                      <div className="flex items-center justify-between px-3 py-1.5 border-b border-gray-700">
                        <span className="text-[10px] text-gray-500">{agentMessages.filter(m => m.role !== '_streaming').length} messages</span>
                        <button
                          onClick={() => {
                            const text = agentMessages.filter(m => m.role !== '_streaming').map(m => `[${m.role}] ${m.content}`).join('\n')
                            navigator.clipboard.writeText(text).then(() => {
                              setDebugCopied(true)
                              const tid = window.setTimeout(() => setDebugCopied(false), 1500)
                              debugCopyTimer.current = tid
                            }).catch(() => {})
                          }}
                          className="text-[10px] text-gray-400 hover:text-gray-200 px-1.5 py-0.5 rounded hover:bg-gray-700"
                        >{debugCopied ? t('tasks.copied') : t('tasks.copyAll')}</button>
                      </div>
                      <div className="p-3 space-y-1">
                      {agentMessages.filter(m => m.role !== '_streaming').map((msg, i) => (
                        <div key={i} className="text-xs font-mono min-w-0">
                          <span className={`inline-block px-1.5 py-0.5 rounded text-[10px] font-bold mr-1.5 ${
                            msg.role === 'assistant' || msg.role === 'result' ? 'bg-green-900 text-green-300' :
                            msg.role === 'user' ? 'bg-blue-900 text-blue-300' :
                            msg.role === 'tool_use' ? 'bg-purple-900 text-purple-300' :
                            msg.role === 'system' ? 'bg-red-900 text-red-300' :
                            'bg-gray-700 text-gray-400'
                          }`}>{msg.role}</span>
                          <span className="text-gray-300 whitespace-pre-wrap break-all">{msg.content}</span>
                        </div>
                      ))}
                      <div ref={messagesEndRef} />
                      </div>
                    </div>
                  ) : isActive ? (
                    <div className="flex items-center gap-3 px-4 py-4 bg-blue-50 rounded-lg border border-blue-200">
                      <div className="w-5 h-5 rounded-full border-2 border-blue-500 border-t-transparent animate-spin shrink-0" />
                      <div className="text-sm font-medium text-blue-700">{t('tasks.agentStarting')}</div>
                    </div>
                  ) : null}
                </div>
              ) : (
                /* Normal mode: conversation stream */
                <ConversationStream
                  items={conversationItems}
                  todoItems={todoItems}
                  task={selectedTask}
                  steps={steps}
                  screenshots={screenshots}
                  pendingQuestions={pendingQuestions}
                  selectedAnswers={selectedAnswers}
                  otherInputs={otherInputs}
                  showOtherInput={showOtherInput}
                  onSelectAnswer={selectAnswer}
                  onToggleOther={toggleOtherInput}
                  onSetOtherAnswer={setOtherAnswer}
                  onSubmitAll={submitAllAnswers}
                  onConfirmPlan={handleConfirmPlan}
                  onRequestChanges={() => {
                    const input = chatInputRef.current
                    if (input) { input.focus(); input.placeholder = t('tasks.planFeedbackPlaceholder') }
                  }}
                  questionBannerRef={questionBannerRef}
                  isActive={isActive}
                  userNearBottom={userNearBottom}
                />
              )}

              {/* Logs */}
              {logs.length > 0 && (
                <div className="mt-6">
                  <h3 className="text-sm font-semibold text-gray-600 mb-2">Logs</h3>
                  <div className="bg-gray-900 text-green-400 rounded-lg p-3 text-xs font-mono max-h-48 overflow-y-auto">
                    {logs.map((log, i) => <div key={i}>{log}</div>)}
                    <div ref={logsEndRef} />
                  </div>
                </div>
              )}
            </div>

            {/* Fixed footer */}
            <div className="bg-white border-t border-gray-200 px-4 py-3 space-y-2">
              {/* Profile selector + Debug toggle row */}
              <div className="flex items-center gap-2">
                <div className="flex items-center gap-1.5 px-2.5 py-1.5 bg-gray-50 border border-gray-200 rounded-lg">
                  <svg className="w-3.5 h-3.5 text-gray-400" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9.75 3.104v5.714a2.25 2.25 0 01-.659 1.591L5 14.5M9.75 3.104c-.251.023-.501.05-.75.082m.75-.082a24.301 24.301 0 014.5 0m0 0v5.714c0 .597.237 1.17.659 1.591L19.8 15.3M14.25 3.104c.251.023.501.05.75.082M19.8 15.3l-1.57.393A9.065 9.065 0 0112 15a9.065 9.065 0 00-6.23.693L5 14.5m14.8.8l1.402 1.402c1.232 1.232.65 3.318-1.067 3.611A48.309 48.309 0 0112 21c-2.773 0-5.491-.235-8.135-.687-1.718-.293-2.3-2.379-1.067-3.61L5 14.5" /></svg>
                  <select
                    value={selectedProfileId}
                    onChange={e => setSelectedProfileId(e.target.value)}
                    className="text-xs bg-transparent border-none outline-none cursor-pointer text-gray-700 pr-4 appearance-none"
                    style={{ backgroundImage: 'url("data:image/svg+xml,%3Csvg xmlns=\'http://www.w3.org/2000/svg\' width=\'12\' height=\'12\' viewBox=\'0 0 24 24\' fill=\'none\' stroke=\'%239ca3af\' stroke-width=\'2\'%3E%3Cpath d=\'M6 9l6 6 6-6\'/%3E%3C/svg%3E")', backgroundRepeat: 'no-repeat', backgroundPosition: 'right 0 center' }}
                  >
                    {[...profiles].sort((a, b) => {
                      const def = currentUser?.default_profile_id || 'default'
                      if (a.id === def) return -1
                      if (b.id === def) return 1
                      return 0
                    }).map(p => (
                      <option key={p.id} value={p.id}>{p.name}</option>
                    ))}
                  </select>
                </div>
                {(() => {
                  const taskProfile = selectedTask.ai_profile_id || 'default'
                  const userDefault = currentUser?.default_profile_id || 'default'
                  const isLegacyMatch = taskProfile === 'default' && selectedProfileId === userDefault
                  return taskProfile !== selectedProfileId && !isLegacyMatch ? (
                    <span className="text-xs text-amber-600">
                      {t('chat.profileMismatch', { current: profiles.find(p => p.id === taskProfile)?.name || taskProfile })}
                    </span>
                  ) : null
                })()}
                <button
                  onClick={() => { setEditingProfile(undefined); setShowProfileModal(true) }}
                  className="text-xs text-gray-400 hover:text-blue-600"
                  title={t('chat.manageProfiles')}
                >
                  <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.066 2.573c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.573 1.066c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.066-2.573c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" /><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" /></svg>
                </button>
                {/* Mode picker: Auto vs. Review plan first */}
                {getUI(selectedTask.status).showModeToggle && (() => {
                  const planMode = selectedTask.plan_mode
                  const description = planMode
                    ? t('tasks.modePlanDesc')
                    : t('tasks.modeAutoDesc')
                  const setMode = async (newVal: boolean) => {
                    if (newVal === planMode) return
                    try {
                      await api.patch(`/api/tasks/${selectedTask.id}/review-plan`, { plan_mode: newVal })
                      setSelectedTask(prev => prev ? { ...prev, plan_mode: newVal } : prev)
                      setTasks(prev => prev.map(t2 => t2.id === selectedTask.id ? { ...t2, plan_mode: newVal } : t2))
                      setCurrentUser(prev => prev ? { ...prev, plan_mode_default: newVal } : prev)
                    } catch { /* ignore */ }
                  }
                  return selectedTask.store_id ? (
                    <div className="flex items-center gap-1.5 flex-shrink-0">
                      <div className="inline-flex items-center rounded-md border border-gray-200 bg-gray-50 p-0.5">
                        <button
                          type="button"
                          aria-pressed={!planMode}
                          onClick={() => setMode(false)}
                          className={`px-2 py-0.5 text-xs rounded transition-colors ${!planMode ? 'bg-white text-gray-800 shadow-sm' : 'text-gray-500 hover:text-gray-700'}`}
                        >
                          {t('tasks.modeAuto')}
                        </button>
                        <button
                          type="button"
                          aria-pressed={planMode}
                          onClick={() => setMode(true)}
                          className={`px-2 py-0.5 text-xs rounded transition-colors ${planMode ? 'bg-white text-gray-800 shadow-sm' : 'text-gray-500 hover:text-gray-700'}`}
                        >
                          {t('tasks.modePlan')}
                        </button>
                      </div>
                      <span className="text-[11px] text-gray-400 hidden md:inline">{description}</span>
                    </div>
                  ) : (
                    <span
                      className="text-xs text-gray-500 flex-shrink-0"
                      title={t('tasks.modeTooltipForced')}
                    >
                      {t('tasks.planModeLabel')}
                    </span>
                  )
                })()}
                <div className="flex-1" />
                <div
                  onClick={() => setDebugMode(d => !d)}
                  onKeyDown={e => { if (e.key === ' ' || e.key === 'Enter') { e.preventDefault(); e.currentTarget.click() } }}
                  tabIndex={0}
                  className="flex items-center gap-1.5 cursor-pointer select-none flex-shrink-0"
                  role="switch"
                  aria-checked={debugMode}
                >
                  <span className="text-xs text-gray-400">{t('tasks.debugMode')}</span>
                  <div className={`relative w-8 h-4 rounded-full transition-colors ${debugMode ? 'bg-blue-500' : 'bg-gray-300'}`}>
                    <div className={`absolute top-0.5 left-0.5 w-3 h-3 bg-white rounded-full transition-transform pointer-events-none ${debugMode ? 'translate-x-4' : ''}`} />
                  </div>
                </div>
              </div>

              {/* Plan feedback hint */}
              {selectedTask.status === 'planned' && selectedTask.plan_mode && !selectedTask.schedule_id && (
                <div className="text-xs text-indigo-500 mb-1">
                  {t('tasks.planFeedbackHint', 'Send feedback to revise the plan, or confirm to execute')}
                </div>
              )}

              {/* Unified send bar */}
              <div className="flex gap-2 items-end">
                <textarea
                  ref={chatInputRef}
                  data-testid="chat-input"
                  rows={1}
                  value={chatInput}
                  onChange={e => setChatInput(e.target.value)}
                  onKeyDown={e => {
                    // Bail on IME composition keystrokes. Chinese /
                    // Japanese / Korean IMEs dispatch an Enter
                    // `keydown` to confirm a composition BEFORE the
                    // real submit Enter ~50ms later — without this
                    // guard both fire sendChatMessage, producing
                    // duplicate user messages. keyCode===229 catches
                    // older Safari / Firefox builds where
                    // isComposing is unreliable.
                    if (e.nativeEvent.isComposing || e.keyCode === 229) return
                    if (e.key === 'Enter' && !e.shiftKey) {
                      e.preventDefault()
                      if (hasText) sendChatMessage()
                    }
                  }}
                  placeholder={getPlaceholder()}
                  disabled={!canSend && !isActive}
                  className="flex-1 px-3 py-2 text-sm leading-5 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:opacity-40 disabled:cursor-not-allowed resize-none overflow-y-auto"
                />
                {canSend && hasText ? (
                  <button
                    onClick={sendChatMessage}
                    className="px-4 py-2 text-sm font-medium text-white bg-indigo-600 rounded-lg hover:bg-indigo-700 transition-colors"
                  >
                    {t('tasks.send')}
                  </button>
                ) : isActive && !hasText ? (
                  <button
                    onClick={stopAgent}
                    className="px-3 py-2 text-sm font-medium text-white bg-red-600 rounded-lg hover:bg-red-700 transition-colors"
                  >
                    {t('tasks.stopTask')}
                  </button>
                ) : (
                  <button
                    disabled
                    className="px-4 py-2 text-sm font-medium text-white bg-gray-300 rounded-lg cursor-not-allowed"
                  >
                    {t('tasks.send')}
                  </button>
                )}
              </div>
            </div>
          </>
        ) : (
          <div className="flex items-center justify-center h-full text-gray-400">
            {taskPanelActive ? t('tasks.selectTaskHint') : t('tasks.selectStoreHint')}
          </div>
        )}
      </div>
      {editingSchedule && (
        <EditScheduleModal
          schedule={editingSchedule}
          onClose={() => setEditingSchedule(null)}
          onUpdated={(updated) => {
            onScheduleUpdated(updated)
            setEditingSchedule(null)
          }}
        />
      )}
    </>
  )
}
