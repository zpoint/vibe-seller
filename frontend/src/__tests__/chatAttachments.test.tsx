/**
 * Chat attachment UX (stage-until-send redesign):
 *  - Picking a file uploads it to STAGING and shows a removable chip —
 *    the message textarea is NEVER polluted with a raw path.
 *  - The chip's ✕ discards the staged file.
 *  - sendChatMessage sends {content, attachment_ids} for any combination
 *    of text and attachments, and renders the server's canonical content.
 *  - A user bubble renders attachment thumbnails, not the path/URL text.
 */
import { describe, it, expect, vi, afterEach } from 'vitest'
import { useState } from 'react'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { TasksView } from '../views/TasksView'
import { MessageBubble } from '../components/conversation/MessageBubble'
import { sendChatMessage } from '../handlers/sendChatMessage'
import type { Task, StagedAttachment, ConversationItem } from '../types'

vi.mock('../lib/telemetry', () => ({
  sendEvent: vi.fn(),
  lengthBucket: () => 'small',
  ageBucket: () => 'new',
}))

// TasksView's subcomponents (SubtaskList etc.) fetch via the api wrapper;
// keep those inert so only the raw-`fetch` staging upload is exercised.
vi.mock('../api', () => ({
  api: {
    get: vi.fn(async () => []),
    post: vi.fn(async () => ({})),
    patch: vi.fn(async () => ({})),
    put: vi.fn(async () => ({})),
    delete: vi.fn(async () => ({})),
  },
}))

function runningTask(): Task {
  return {
    id: 'task-1', store_id: 'store-1', title: 'T', description: null,
    status: 'completed', plan: null, result: null, todos: null,
    wait_condition: null, error: null, plan_mode: false,
    ai_profile_id: 'default', schedule_id: null, batch_id: null,
    created_at: '', started_at: null, completed_at: null, is_plan_only: false,
  } as Task
}

function makeProps(over: Record<string, unknown> = {}) {
  const noop = vi.fn()
  return {
    taskPanelActive: true, taskPanelTitle: 'Tasks', tasks: [],
    selectedTask: runningTask(), steps: [], screenshots: [], logs: [],
    agentMessages: [], todoItems: [], pendingQuestions: null,
    conversationItems: [], selectedAnswers: {}, otherInputs: {},
    showOtherInput: {}, chatInput: '', setChatInput: noop,
    chatAttachments: [], setChatAttachments: noop, debugMode: false,
    setDebugMode: noop, profiles: [], selectedProfileId: 'default',
    setSelectedProfileId: noop, currentUser: null, showAllTasks: false,
    openCreateModal: noop, selectTask: noop, stopAgent: noop,
    retryTask: noop, continueTask: noop, deleteTask: noop, selectAnswer: noop,
    toggleOtherInput: noop, setOtherAnswer: noop, submitAllAnswers: noop,
    sendChatMessage: noop, setSelectedTask: noop, setTasks: noop,
    setCurrentUser: noop, setEditingProfile: noop, setShowProfileModal: noop,
    questionBannerRef: { current: null }, taskSubTab: 'tasks',
    setTaskSubTab: noop, schedules: [], selectedSchedule: null,
    scheduleTasks: [], showCreateSchedule: false, setShowCreateSchedule: noop,
    selectSchedule: noop, deleteSchedule: noop, toggleSchedulePause: noop,
    triggerSchedule: noop, replanSchedule: noop, setSelectedSchedule: noop,
    onScheduleUpdated: noop, selectedStore: null, stores: [],
    ...over,
  }
}

// Stateful wrapper so setChatAttachments actually updates rendered chips.
function Harness(props: Record<string, unknown>) {
  const [atts, setAtts] = useState<StagedAttachment[]>([])
  const [input, setInput] = useState('')
  return (
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    <TasksView {...(props as any)} chatInput={input} setChatInput={setInput}
      chatAttachments={atts} setChatAttachments={setAtts} />
  )
}

describe('chat attachment chips (stage-until-send)', () => {
  afterEach(() => vi.restoreAllMocks())

  it('picking a file shows a chip and leaves the textarea untouched', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({
      ok: true,
      json: async () => ({
        id: 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
        filename: 'photo.png', content_type: 'image/png',
        url: '/api/tasks/task-1/staged/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
      }),
    })))
    render(<Harness {...makeProps()} />)

    const input = screen.getByTestId('chat-file-input')
    const file = new File([new Uint8Array([1, 2, 3])], 'photo.png', { type: 'image/png' })
    fireEvent.change(input, { target: { files: [file] } })

    const chip = await screen.findByTestId('chat-attachment-chip')
    expect(chip).toHaveTextContent('photo.png')
    // The textarea must NOT contain the path/URL.
    const textarea = screen.getByTestId('chat-input') as HTMLTextAreaElement
    expect(textarea.value).toBe('')
    expect(textarea.value).not.toContain('/api/tasks')
    // POSTed to the STAGING endpoint (outside the agent cwd), not the workspace.
    expect((globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0][0])
      .toBe('/api/tasks/task-1/staged')
  })

  it('the chip ✕ removes the staged attachment', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({
      ok: true,
      json: async () => ({
        id: 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',
        filename: 'doc.pdf', content_type: 'application/pdf',
        url: '/api/tasks/task-1/staged/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',
      }),
    })))
    render(<Harness {...makeProps()} />)
    fireEvent.change(screen.getByTestId('chat-file-input'), {
      target: { files: [new File(['x'], 'doc.pdf', { type: 'application/pdf' })] },
    })
    await screen.findByTestId('chat-attachment-chip')
    fireEvent.click(screen.getByTestId('chat-attachment-remove'))
    await waitFor(() =>
      expect(screen.queryByTestId('chat-attachment-chip')).toBeNull())
  })
})

describe('sendChatMessage: text / attachment / both combinations', () => {
  function baseDeps(over: Record<string, unknown> = {}) {
    const post = vi.fn(async () => ({ content: 'echo' }))
    return {
      deps: {
        api: { post },
        selectedTask: runningTask(),
        chatInput: '', attachments: [] as StagedAttachment[],
        profileId: 'default', conversationItems: [] as ConversationItem[],
        sendingRef: { current: false },
        setChatInput: vi.fn(), setAttachments: vi.fn(),
        setAgentMessages: vi.fn(), setConversationItems: vi.fn(),
        setSelectedTask: vi.fn(), setTasks: vi.fn(),
        ...over,
      },
      post,
    }
  }

  it('does nothing when there is neither text nor attachments', async () => {
    const { deps, post } = baseDeps()
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    await sendChatMessage(deps as any)
    expect(post).not.toHaveBeenCalled()
  })

  it('sends text only (no attachment ids)', async () => {
    const { deps, post } = baseDeps({ chatInput: '  hello  ' })
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    await sendChatMessage(deps as any)
    expect(post).toHaveBeenCalledWith('/api/tasks/task-1/messages', {
      content: 'hello', profile_id: 'default', attachment_ids: [],
    })
  })

  it('sends attachments only (empty content)', async () => {
    const atts: StagedAttachment[] = [
      { id: 'id1', filename: 'a.png', contentType: 'image/png', url: '/u1' },
    ]
    const { deps, post } = baseDeps({ chatInput: '', attachments: atts })
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    await sendChatMessage(deps as any)
    expect(post).toHaveBeenCalledWith('/api/tasks/task-1/messages', {
      content: '', profile_id: 'default', attachment_ids: ['id1'],
    })
    // Clears input + staged attachments after send.
    expect(deps.setChatInput).toHaveBeenCalledWith('')
    expect(deps.setAttachments).toHaveBeenCalledWith([])
  })

  it('sends text + attachments together', async () => {
    const atts: StagedAttachment[] = [
      { id: 'id1', filename: 'a.png', contentType: 'image/png', url: '/u1' },
      { id: 'id2', filename: 'b.pdf', contentType: 'application/pdf', url: '/u2' },
    ]
    const { deps, post } = baseDeps({ chatInput: 'look', attachments: atts })
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    await sendChatMessage(deps as any)
    expect(post).toHaveBeenCalledWith('/api/tasks/task-1/messages', {
      content: 'look', profile_id: 'default', attachment_ids: ['id1', 'id2'],
    })
  })
})

describe('MessageBubble renders attachments as thumbnails, not paths', () => {
  it('renders an <img> for a markdown-image attachment and hides the URL text', () => {
    const content = 'here it is\n![photo.png](/api/tasks/task-1/files/uploads/photo.png)'
    render(<MessageBubble role="user" content={content} />)
    const img = screen.getByRole('img', { name: 'photo.png' })
    expect(img).toHaveAttribute('src', '/api/tasks/task-1/files/uploads/photo.png')
    expect(screen.getByText('here it is')).toBeInTheDocument()
    // The raw markdown / path is not shown as text.
    expect(screen.queryByText(/!\[/)).toBeNull()
  })

  it('renders a PDF chip (not a broken image) for a pdf attachment', () => {
    const content = '![doc.pdf](/api/tasks/task-1/files/uploads/doc.pdf)'
    render(<MessageBubble role="user" content={content} />)
    expect(screen.queryByRole('img')).toBeNull()
    expect(screen.getByText('doc.pdf')).toBeInTheDocument()
  })
})
