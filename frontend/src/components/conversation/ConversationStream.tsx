import { useState, useEffect, useRef, useCallback, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { api } from '../../api'
import { PlanCard } from './PlanCard'
import { TaskStartCard } from './TaskStartCard'
import { MessageBubble } from './MessageBubble'
import { ExecutionSeparator } from './ExecutionSeparator'
import { ToolCallGroup } from './ToolCallCard'
import { ThinkingBlock, WorkingIndicator } from './ThinkingBlock'
import { QuestionBanner } from '../QuestionBanner'
import { ImageRequestCard } from './ImageRequestCard'
import { GeneratedImageCard } from './GeneratedImageCard'
import { StepIcon } from '../ui'
import type { ConversationItem, TodoItem, TaskStep, Task } from '../../types'

type DisplayItem =
  | { kind: 'item'; item: ConversationItem }
  | { kind: 'tool_group'; items: ConversationItem[] }

/** Group consecutive tool_call items at render time. */
function groupItems(items: ConversationItem[]): DisplayItem[] {
  const result: DisplayItem[] = []
  let toolBuf: ConversationItem[] = []

  const flushTools = () => {
    if (toolBuf.length === 0) return
    if (toolBuf.length === 1) {
      result.push({ kind: 'tool_group', items: [...toolBuf] })
    } else {
      result.push({ kind: 'tool_group', items: [...toolBuf] })
    }
    toolBuf = []
  }

  for (const item of items) {
    if (item.type === 'tool_call') {
      toolBuf.push(item)
    } else {
      flushTools()
      result.push({ kind: 'item', item })
    }
  }
  flushTools()
  return result
}

// Must match the prompt format in app/routers/tasks.py
// (_auto_run_task ~L187 and design_task ~L940).
// Falls back to plain MessageBubble if format drifts.
const TASK_START_PREFIX = 'Design an execution plan for this task: '

function parseTaskStart(
  content: string,
): { title: string; description?: string } | null {
  if (!content.startsWith(TASK_START_PREFIX)) return null
  const rest = content.slice(TASK_START_PREFIX.length)
  const sepIdx = rest.indexOf('\n\nDetails: ')
  if (sepIdx === -1) return { title: rest }
  return {
    title: rest.slice(0, sepIdx),
    description: rest.slice(sepIdx + '\n\nDetails: '.length) || undefined,
  }
}

/** Collapsed view for questions that have already been answered. */
function AnsweredQuestions({ questions }: { questions: { header?: string; question: string }[] }) {
  const { t } = useTranslation()
  const [expanded, setExpanded] = useState(false)
  const summary = questions.map(q => q.header || q.question.slice(0, 40)).join(', ')

  return (
    <div className="border-l-2 border-amber-200 pl-2 py-1">
      <button
        className="flex items-center gap-1.5 text-xs text-gray-400 hover:text-amber-600 w-full text-left"
        onClick={() => setExpanded(e => !e)}
      >
        <span className={`text-[10px] transition-transform ${expanded ? 'rotate-90' : ''}`}>
          ▶
        </span>
        <span className="text-amber-500">
          {t('tasks.answeredQuestions', 'Questions answered')}
        </span>
        {!expanded && (
          <span className="text-gray-300 truncate">
            — {summary}
          </span>
        )}
      </button>
      {expanded && (
        <div className="ml-5 mt-1 space-y-1">
          {questions.map((q, i) => (
            <div key={i} className="text-[11px] text-gray-400">
              {q.header && (
                <span className="text-amber-500 font-medium mr-1">{q.header}:</span>
              )}
              {q.question.length > 80 ? q.question.slice(0, 80) + '...' : q.question}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

interface TaskFileEntry {
  name: string
  size: number
  type: string
  modified_at: string
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

interface FileTreeNode {
  name: string
  fullPath: string
  isDir: boolean
  size?: number
  type?: string
  children: FileTreeNode[]
}

function buildFileTree(files: TaskFileEntry[]): FileTreeNode[] {
  const root: FileTreeNode = { name: '', fullPath: '', isDir: true, children: [] }
  for (const f of files) {
    const parts = f.name.split('/')
    let node = root
    for (let i = 0; i < parts.length; i++) {
      const part = parts[i]
      const isLast = i === parts.length - 1
      let child = node.children.find(c => c.name === part)
      if (!child) {
        child = {
          name: part,
          fullPath: parts.slice(0, i + 1).join('/'),
          isDir: !isLast,
          size: isLast ? f.size : undefined,
          type: isLast ? f.type : undefined,
          children: [],
        }
        node.children.push(child)
      }
      node = child
    }
  }
  return root.children
}

function FileTreeItem({ node, taskId, depth }: { node: FileTreeNode; taskId: string; depth: number }) {
  const [expanded, setExpanded] = useState(true)
  const paddingLeft = `${depth * 16 + 8}px`

  if (node.isDir) {
    return (
      <div>
        <button
          onClick={() => setExpanded(p => !p)}
          className="flex items-center gap-1.5 w-full px-2 py-1 text-xs text-gray-600 hover:bg-gray-100 rounded transition-colors cursor-pointer"
          style={{ paddingLeft }}
        >
          <span className="text-[10px] w-3 text-center">{expanded ? '\u25BE' : '\u25B8'}</span>
          <span className="text-gray-400">{'\uD83D\uDCC1'}</span>
          <span className="font-medium">{node.name}/</span>
          <span className="text-gray-400 ml-auto">{node.children.length}</span>
        </button>
        {expanded && node.children.map(child => (
          <FileTreeItem key={child.fullPath} node={child} taskId={taskId} depth={depth + 1} />
        ))}
      </div>
    )
  }

  const encodedPath = node.fullPath.split('/').map(encodeURIComponent).join('/')
  return (
    <a
      href={`/api/tasks/${taskId}/files/${encodedPath}`}
      download={node.name}
      className="flex items-center gap-1.5 px-2 py-1 text-xs hover:bg-indigo-50 rounded transition-colors"
      style={{ paddingLeft: `${(depth) * 16 + 8 + 12 + 6}px` }}
    >
      <span className="text-gray-400 text-[10px]">
        {node.type?.includes('pdf') ? '\uD83D\uDCC4' : '\uD83D\uDCCE'}
      </span>
      <span className="text-indigo-600 font-medium truncate">{node.name}</span>
      <span className="text-gray-400 ml-auto whitespace-nowrap">
        {node.size != null ? formatSize(node.size) : ''}
      </span>
    </a>
  )
}

function TaskFiles({ taskId }: { taskId: string }) {
  const { t } = useTranslation()
  const [files, setFiles] = useState<TaskFileEntry[]>([])
  const [loaded, setLoaded] = useState(false)

  const load = useCallback(() => {
    api.get(`/api/tasks/${taskId}/files`)
      .then((data: TaskFileEntry[]) => { setFiles(data); setLoaded(true) })
      .catch(() => setLoaded(true))
  }, [taskId])

  useEffect(() => { load() }, [load])

  if (!loaded || files.length === 0) return null

  const tree = buildFileTree(files)

  return (
    <div className="mt-3 bg-gray-50 rounded-lg border border-gray-200 p-3">
      <div className="flex items-center justify-between mb-2">
        <h4 className="text-xs font-medium text-gray-500">
          {t('tasks.generatedFiles', 'Generated Files')}
        </h4>
        <a
          href={`/api/tasks/${taskId}/files-zip`}
          download
          className="text-[10px] text-indigo-600 hover:underline cursor-pointer"
        >
          {t('tasks.downloadZip', 'Download All (ZIP)')}
        </a>
      </div>
      <div className="space-y-0.5">
        {tree.map(node => (
          <FileTreeItem key={node.fullPath} node={node} taskId={taskId} depth={0} />
        ))}
      </div>
    </div>
  )
}

const PROSE_RESULT = 'prose prose-neutral max-w-none text-[17px] leading-[1.65] prose-code:text-[14px] prose-code:font-mono prose-code:text-gray-800 prose-code:bg-gray-100 prose-code:px-1 prose-code:py-0.5 prose-code:rounded prose-code:before:content-none prose-code:after:content-none prose-pre:bg-gray-50 prose-pre:text-gray-800 prose-pre:border prose-pre:border-gray-200 prose-pre:rounded prose-pre:p-3 prose-pre:my-3 prose-pre:text-[14px] prose-h1:text-[20px] prose-h1:font-bold prose-h1:text-gray-900 prose-h1:border-b prose-h1:border-gray-200 prose-h1:pb-2 prose-h1:mb-3 prose-h2:text-[17px] prose-h2:font-semibold prose-h2:text-gray-800 prose-h2:border-b prose-h2:border-gray-100 prose-h2:pb-1.5 prose-h2:mb-2.5 prose-h2:mt-5 prose-p:my-2.5 prose-p:text-gray-800 prose-p:leading-[1.65] prose-ul:my-2.5 prose-ul:pl-6 prose-li:my-1 prose-li:marker:text-gray-400'

interface ConversationStreamProps {
  items: ConversationItem[]
  todoItems: TodoItem[]
  task: Task
  steps: TaskStep[]
  screenshots: Record<string, string>
  pendingQuestions: { request_id: string; questions: { header?: string; question: string; options?: { label: string; description?: string }[] }[] } | null
  selectedAnswers: Record<string, string>
  otherInputs: Record<string, string>
  showOtherInput: Record<string, boolean>
  onSelectAnswer: (questionText: string, answer: string) => void
  onToggleOther: (questionText: string) => void
  onSetOtherAnswer: (questionText: string, text: string) => void
  onSubmitAll: (overrideAnswers?: Record<string, string>) => void
  onConfirmPlan: () => void
  onRequestChanges?: () => void
  onImageDecision?: (
    requestId: string,
    action: 'confirm' | 'cancel',
    prompt: string,
    model: string,
    addedReferences: string[],
  ) => void
  questionBannerRef: React.RefObject<HTMLDivElement | null>
  isActive: boolean
  userNearBottom?: React.RefObject<boolean>
  onOpenVisionSetup?: () => void
}

export function ConversationStream({
  items,
  todoItems,
  task,
  steps,
  screenshots,
  pendingQuestions,
  selectedAnswers,
  otherInputs,
  showOtherInput,
  onSelectAnswer,
  onToggleOther,
  onSetOtherAnswer,
  onSubmitAll,
  onConfirmPlan,
  onRequestChanges,
  onImageDecision,
  questionBannerRef,
  isActive,
  userNearBottom,
  onOpenVisionSetup,
}: ConversationStreamProps) {
  const { t } = useTranslation()
  const bottomRef = useRef<HTMLDivElement>(null)

  const lastItem = items[items.length - 1]
  const streamingContent = lastItem?.type === 'streaming' ? lastItem.message?.content : undefined

  useEffect(() => {
    if (!userNearBottom || userNearBottom.current) {
      bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
    }
  }, [items.length, streamingContent, userNearBottom])

  const hasExecutionSep = items.some(i => i.type === 'execution_separator')

  return (
    <div className="space-y-5">
      {/* Review plan toggle in stream when applicable */}

      {items.length === 0 && isActive && (
        <div className="flex items-center gap-3 px-4 py-4 bg-indigo-50 rounded-lg border border-indigo-200">
          <div className="w-5 h-5 rounded-full border-2 border-indigo-500 border-t-transparent animate-spin shrink-0" />
          <div>
            <div className="text-sm font-medium text-indigo-700">
              {task.status === 'designing' ? t('tasks.designing') : t('tasks.running')}
            </div>
            <div className="text-xs text-indigo-500">{t('tasks.agentStarting')}</div>
          </div>
        </div>
      )}

      {(() => {
        let taskStartRendered = false
        const allPlanVersions = items.filter(i => i.type === 'plan' && i.plan).map(i => i.plan!)
        const renderDI = (di: DisplayItem, idx: number): ReactNode => {
        if (di.kind === 'tool_group') {
          return <ToolCallGroup key={`tg-${idx}`} items={di.items.map(i => i.toolCall!)} />
        }
        const item = di.item
        switch (item.type) {
          case 'plan':
            return (
              <PlanCard
                key={item.id}
                plan={item.plan!}
                allVersions={item.plan!.isCurrent ? allPlanVersions : undefined}
                todoItems={todoItems}
                taskStatus={task.status}
                planMode={task.plan_mode}
                scheduleId={task.schedule_id}
                onConfirm={item.plan!.isCurrent ? onConfirmPlan : undefined}
                onRequestChanges={item.plan!.isCurrent ? onRequestChanges : undefined}
              />
            )
          case 'user_message': {
            if (!taskStartRendered) {
              const parsed = parseTaskStart(item.message!.content)
              if (parsed) {
                taskStartRendered = true
                return (
                  <TaskStartCard
                    key={item.id}
                    title={parsed.title}
                    description={parsed.description}
                  />
                )
              }
            }
            return <MessageBubble key={item.id} role="user" content={item.message!.content} />
          }
          case 'agent_message':
            return <MessageBubble key={item.id} role="assistant" content={item.message!.content} onOpenVisionSetup={onOpenVisionSetup} />
          case 'streaming':
            return <MessageBubble key={item.id} role="_streaming" content={item.message!.content} onOpenVisionSetup={onOpenVisionSetup} />
          case 'execution_separator':
            return <ExecutionSeparator key={item.id} />
          case 'thinking':
            return (
              <ThinkingBlock
                key={item.id}
                content={item.thinking!.content}
                isStreaming={item.thinking!.isStreaming}
              />
            )
          case 'result':
            return (
              <div key={item.id}>
                <div className="bg-white rounded-2xl border border-gray-200 shadow-sm p-5 sm:p-6">
                  <div className="flex items-center gap-2 mb-3">
                    <span className="w-1.5 h-1.5 rounded-full bg-green-500" />
                    <span className="text-[11px] font-semibold uppercase tracking-[0.08em] text-green-600">{t('tasks.result')}</span>
                  </div>
                  <div className={`text-gray-800 ${PROSE_RESULT} overflow-x-auto`}>
                    <Markdown remarkPlugins={[remarkGfm]}>{item.result || ''}</Markdown>
                  </div>
                </div>
              </div>
            )
          case 'question': {
            const isActive = pendingQuestions?.request_id === item.questions?.request_id
            if (isActive) {
              return (
                <QuestionBanner
                  key={item.id}
                  bannerRef={questionBannerRef}
                  questions={item.questions!.questions}
                  selectedAnswers={selectedAnswers}
                  showOtherInput={showOtherInput}
                  otherInputs={otherInputs}
                  onSelectAnswer={onSelectAnswer}
                  onToggleOther={onToggleOther}
                  onSetOtherAnswer={onSetOtherAnswer}
                  onSubmitAll={onSubmitAll}
                />
              )
            }
            return (
              <AnsweredQuestions
                key={item.id}
                questions={item.questions!.questions}
              />
            )
          }
          case 'image_request':
            return (
              <ImageRequestCard
                key={item.id}
                taskId={task.id}
                requestId={item.imageRequest!.requestId}
                prompt={item.imageRequest!.prompt}
                model={item.imageRequest!.model}
                models={item.imageRequest!.models}
                referenceImages={item.imageRequest!.referenceImages}
                kind={item.imageRequest!.kind}
                resolved={item.imageRequest!.resolved}
                expired={item.imageRequest!.expired}
                interrupted={item.imageRequest!.interrupted}
                generating={item.imageRequest!.generating}
                onDecision={onImageDecision || (() => {})}
              />
            )
          case 'generated_image':
            return (
              <GeneratedImageCard
                key={item.id}
                url={item.generatedImage!.url}
                path={item.generatedImage!.path}
                prompt={item.generatedImage!.prompt}
                model={item.generatedImage!.model}
                kind={item.generatedImage!.kind}
              />
            )
          default:
            return null
        }
        }

        // Thread consecutive assistant-side items (thinking / tools /
        // agent text / streaming) onto one vertical spine so a single
        // eye-line follows the whole turn — matches opencode's part rail.
        // User messages, plans, results, separators and questions render
        // standalone at full column width.
        const isAssistantSide = (di: DisplayItem) =>
          di.kind === 'tool_group' ||
          (di.kind === 'item' &&
            (di.item.type === 'agent_message' ||
              di.item.type === 'streaming' ||
              di.item.type === 'thinking'))

        const dis = groupItems(items)
        const out: ReactNode[] = []
        let turnBuf: { di: DisplayItem; idx: number }[] = []
        const flushTurn = (live: boolean) => {
          if (turnBuf.length === 0) return
          const buf = turnBuf
          turnBuf = []
          out.push(
            <div key={`turn-${buf[0].idx}`} className="flex gap-3.5">
              <div className={`w-[3px] shrink-0 rounded-full ${live ? 'bg-indigo-500' : 'bg-gray-200'}`} />
              <div className="min-w-0 flex-1 space-y-3">
                {buf.map(b => renderDI(b.di, b.idx))}
              </div>
            </div>,
          )
        }
        dis.forEach((di, idx) => {
          if (isAssistantSide(di)) {
            turnBuf.push({ di, idx })
          } else {
            flushTurn(false)
            out.push(renderDI(di, idx))
          }
        })
        // Last assistant turn glows while the agent is still working.
        flushTurn(isActive)
        return out
      })()}

      {/* Inline pending questions if not already in stream */}
      {pendingQuestions && pendingQuestions.questions.length > 0 &&
        !items.some(i => i.type === 'question' && i.questions?.request_id === pendingQuestions.request_id) && (
        <QuestionBanner
          bannerRef={questionBannerRef}
          questions={pendingQuestions.questions}
          selectedAnswers={selectedAnswers}
          showOtherInput={showOtherInput}
          otherInputs={otherInputs}
          onSelectAnswer={onSelectAnswer}
          onToggleOther={onToggleOther}
          onSetOtherAnswer={onSetOtherAnswer}
          onSubmitAll={onSubmitAll}
        />
      )}

      {/* Waiting info card */}
      {task.status === 'waiting' && task.wait_condition && (() => {
        try {
          const cond = JSON.parse(task.wait_condition)
          const strategy = cond.check_strategy || 'manual'
          const waitingSince = cond.waiting_since ? new Date(cond.waiting_since) : null
          const nextCheck = cond.next_check_at ? new Date(cond.next_check_at) : null
          const maxDays = cond.max_wait_days || 30
          // eslint-disable-next-line react-hooks/purity -- relative time display needs current time
          const daysSince = waitingSince ? Math.floor((Date.now() - waitingSince.getTime()) / 86400000) : 0
          const daysRemaining = Math.max(0, maxDays - daysSince)
          const keywords: string[] = cond.keywords || []
          return (
            <div className="bg-amber-50 rounded-lg border border-amber-200 p-4">
              <div className="flex items-center gap-2 mb-3">
                <span className="text-amber-600 text-lg">&#9203;</span>
                <h3 className="text-sm font-semibold text-amber-800">{t('status.waiting')}</h3>
                <span className={`px-1.5 py-0.5 text-[10px] rounded-full font-medium ${strategy === 'email' ? 'bg-indigo-100 text-indigo-700' : 'bg-gray-100 text-gray-600'}`}>
                  {strategy === 'email' ? t('waiting.autoEmail') : t('waiting.manual')}
                </span>
              </div>
              <div className="space-y-2 text-sm">
                <div>
                  <span className="text-amber-700 font-medium">{t('waiting.reason')}:</span>
                  <span className="ml-2 text-gray-700">{cond.reason}</span>
                </div>
                {waitingSince && (
                  <div>
                    <span className="text-amber-700 font-medium">{t('waiting.since')}:</span>
                    <span className="ml-2 text-gray-600">{waitingSince.toLocaleString()}</span>
                  </div>
                )}
                {strategy === 'email' && nextCheck && (
                  <div>
                    <span className="text-amber-700 font-medium">{t('waiting.nextCheck')}:</span>
                    <span className="ml-2 text-gray-600">{nextCheck.toLocaleString()}</span>
                  </div>
                )}
                {keywords.length > 0 && (
                  <div>
                    <span className="text-amber-700 font-medium">{t('waiting.keywords')}:</span>
                    <span className="ml-2">{keywords.map((kw: string, i: number) => (
                      <span key={i} className="inline-block px-1.5 py-0.5 bg-amber-100 text-amber-800 text-xs rounded mr-1 mb-0.5">{kw}</span>
                    ))}</span>
                  </div>
                )}
                <div>
                  <span className="text-amber-700 font-medium">{t('waiting.maxDays')}:</span>
                  <span className="ml-2 text-gray-600">{t('waiting.daysRemaining', { days: daysRemaining })}</span>
                </div>
              </div>
            </div>
          )
        } catch { return null }
      })()}

      {/* Steps + screenshots (after execution separator) */}
      {hasExecutionSep && steps.length > 0 && (
        <div>
          <h3 className="text-sm font-semibold text-gray-600 mb-3">Steps</h3>
          <div className="space-y-4">
            {steps.map(step => (
              <div key={step.id} className="bg-white rounded-lg border border-gray-200 p-4">
                <div className="flex items-center gap-2 mb-2">
                  <StepIcon status={step.status} />
                  <span className="font-medium text-sm">Step {step.step_index + 1}: {step.name}</span>
                </div>
                {step.error && <div className="text-xs text-red-600 mb-2">{step.error}</div>}
                {screenshots[step.id] && (
                  <img
                    src={`data:image/png;base64,${screenshots[step.id]}`}
                    alt={`Screenshot for step ${step.step_index + 1}`}
                    className="w-full rounded border border-gray-200 mt-2"
                  />
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Working indicator when active and no active streaming */}
      {isActive && lastItem?.type !== 'streaming' &&
        !(lastItem?.type === 'thinking' && lastItem.thinking?.isStreaming) && (
        <WorkingIndicator />
      )}

      {(task.status === 'completed' || task.status === 'failed') && <TaskFiles taskId={task.id} />}

      <div ref={bottomRef} />
    </div>
  )
}
