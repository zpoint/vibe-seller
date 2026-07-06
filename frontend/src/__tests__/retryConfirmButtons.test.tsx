/**
 * The failed-task action row shows THREE buttons: 继续 · 重试 · 删除.
 *  - 继续 (Continue) fires immediately, NO confirm dialog.
 *  - 重试 (Retry) is destructive → must confirm; only fires on OK.
 *  - 删除 (Delete) unchanged (its own confirm lives in App.tsx).
 */
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { TasksView } from '../views/TasksView'
import type { Task } from '../types'

function failedTask(): Task {
  return {
    id: 'task-fail-1',
    store_id: null,
    title: 'Failed task',
    description: null,
    status: 'failed',
    plan: null,
    result: null,
    todos: null,
    wait_condition: null,
    error: 'boom',
    plan_mode: false,
    ai_profile_id: 'default',
    schedule_id: null,
    batch_id: null,
    created_at: '',
    started_at: null,
    completed_at: null,
    is_plan_only: false,
  } as Task
}

function makeProps(over: Record<string, unknown> = {}) {
  const noop = vi.fn()
  return {
    taskPanelActive: true,
    taskPanelTitle: 'Tasks',
    tasks: [],
    selectedTask: failedTask(),
    steps: [],
    screenshots: [],
    logs: [],
    agentMessages: [],
    todoItems: [],
    pendingQuestions: null,
    conversationItems: [],
    selectedAnswers: {},
    otherInputs: {},
    showOtherInput: {},
    chatInput: '',
    setChatInput: noop,
    debugMode: false,
    setDebugMode: noop,
    profiles: [],
    selectedProfileId: 'default',
    setSelectedProfileId: noop,
    currentUser: null,
    showAllTasks: false,
    openCreateModal: noop,
    selectTask: noop,
    stopAgent: noop,
    retryTask: vi.fn(),
    continueTask: vi.fn(),
    deleteTask: vi.fn(),
    selectAnswer: noop,
    toggleOtherInput: noop,
    setOtherAnswer: noop,
    submitAllAnswers: noop,
    sendChatMessage: noop,
    setSelectedTask: noop,
    setTasks: noop,
    setCurrentUser: noop,
    setEditingProfile: noop,
    setShowProfileModal: noop,
    questionBannerRef: { current: null },
    taskSubTab: 'tasks',
    setTaskSubTab: noop,
    schedules: [],
    selectedSchedule: null,
    scheduleTasks: [],
    showCreateSchedule: false,
    setShowCreateSchedule: noop,
    selectSchedule: noop,
    deleteSchedule: noop,
    toggleSchedulePause: noop,
    triggerSchedule: noop,
    replanSchedule: noop,
    setSelectedSchedule: noop,
    onScheduleUpdated: noop,
    selectedStore: null,
    stores: [],
    ...over,
  }
}

describe('failed-task action buttons: 继续 · 重试 · 删除', () => {
  afterEach(() => vi.restoreAllMocks())

  it('继续 resumes immediately with no confirm', () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
    const props = makeProps()
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    render(<TasksView {...(props as any)} />)
    fireEvent.click(screen.getByRole('button', { name: /Continue|继续/ }))
    expect(props.continueTask).toHaveBeenCalledWith('task-fail-1')
    expect(confirmSpy).not.toHaveBeenCalled()
  })

  it('重试 does NOT fire when confirm is cancelled', () => {
    vi.spyOn(window, 'confirm').mockReturnValue(false)
    const props = makeProps()
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    render(<TasksView {...(props as any)} />)
    fireEvent.click(screen.getByRole('button', { name: /^Retry$|^重试$/ }))
    expect(props.retryTask).not.toHaveBeenCalled()
  })

  it('重试 fires only after the confirm is accepted', () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
    const props = makeProps()
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    render(<TasksView {...(props as any)} />)
    fireEvent.click(screen.getByRole('button', { name: /^Retry$|^重试$/ }))
    expect(confirmSpy).toHaveBeenCalledTimes(1)
    expect(props.retryTask).toHaveBeenCalledWith('task-fail-1')
  })
})
