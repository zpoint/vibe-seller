import { memo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { ConversationItem } from '../../types'
import {
  buildRows,
  describeStep,
  liveLabel,
  rowLabel,
  stepLabel,
  summarizeRun,
  thoughtLine,
  type Row,
  type Step,
} from '../../lib/activity'

export function Chevron({ open, dir = 'right' }: { open: boolean; dir?: 'right' | 'down' }) {
  const turn = dir === 'right' ? (open ? 'rotate-90' : '') : (open ? 'rotate-180' : '')
  return (
    <svg
      viewBox="0 0 16 16"
      className={`w-3.5 h-3.5 shrink-0 transition-transform ${turn}`}
      aria-hidden="true"
    >
      <path
        d={dir === 'right' ? 'M6 4l4 4-4 4' : 'M4 6l4 4 4-4'}
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}

/** The raw call behind a step: the command for Bash, the input otherwise. */
function StepDetail({ step }: { step: Step }) {
  const cmd = step.tool === 'Bash' ? step.input?.['command'] : undefined
  const body = typeof cmd === 'string'
    ? cmd
    : step.input ? JSON.stringify(step.input, null, 2) : ''
  if (!body) return null
  return (
    <pre className="text-[12px] leading-[1.5] text-gray-600 bg-gray-50 border border-gray-100 rounded-md px-2.5 py-2 mt-1 overflow-x-auto max-h-64 whitespace-pre-wrap break-all">
      {body}
    </pre>
  )
}

/** A clickable label that reveals the call behind it. */
function StepButton({ label, step, size = 'text-[13px] text-gray-500' }: { label: string; step: Step; size?: string }) {
  const [open, setOpen] = useState(false)
  return (
    <div>
      <button
        className={`flex items-center gap-1 ${size} hover:text-gray-800 max-w-full min-w-0 text-left`}
        onClick={() => setOpen(o => !o)}
      >
        <span className="truncate">{label}</span>
        <Chevron open={open} />
      </button>
      {open && <StepDetail step={step} />}
    </div>
  )
}

/** A lone tool call between messages: one line, click for the call. */
export function ToolCallLine({ tool, input }: { tool: string; input?: Record<string, unknown> }) {
  const { t } = useTranslation()
  const step = describeStep(tool, input)
  return (
    <div className="py-0.5">
      <StepButton label={stepLabel(step, t)} step={step} size="text-[14px] text-gray-500" />
    </div>
  )
}

function ThoughtRow({ item }: { item: ConversationItem }) {
  const [open, setOpen] = useState(false)
  const content = item.thinking?.content || ''
  return (
    <div className="px-3.5 py-2">
      <button
        className="flex items-center gap-1 text-[13px] italic text-gray-400 hover:text-gray-600 max-w-full min-w-0 text-left"
        onClick={() => setOpen(o => !o)}
      >
        <span className="truncate">{thoughtLine(content) || '…'}</span>
        <Chevron open={open} />
      </button>
      {open && (
        <div className="text-[13px] leading-[1.6] text-gray-500 mt-1 whitespace-pre-wrap max-h-64 overflow-y-auto">
          {content}
        </div>
      )}
    </div>
  )
}

function StepsRow({ row }: { row: Extract<Row, { kind: 'steps' }> }) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  if (row.steps.length === 1) {
    return (
      <div className="px-3.5 py-2">
        <StepButton label={rowLabel(row, t)} step={row.steps[0]} size="text-[14px] text-gray-600" />
      </div>
    )
  }
  return (
    <div className="px-3.5 py-2">
      <button
        className="flex items-center gap-1 text-[14px] text-gray-600 hover:text-gray-900 max-w-full min-w-0 text-left"
        onClick={() => setOpen(o => !o)}
      >
        <span className="truncate">{rowLabel(row, t)}</span>
        <Chevron open={open} />
      </button>
      {open && (
        <div className="mt-1 ml-1 pl-3 border-l border-gray-200 space-y-0.5">
          {row.steps.map((s, i) => (
            <StepButton key={row.items[i].id} label={stepLabel(s, t)} step={s} />
          ))}
        </div>
      )}
    </div>
  )
}

interface ActivityGroupProps {
  /** A run of consecutive `thinking` / `tool_call` items. */
  items: ConversationItem[]
  /** The agent is still working on this run (last run of a live task). */
  live?: boolean
}

/**
 * One line for a run of the agent's working steps, saying what was done
 * ("Read notes.md, ran 2 commands, used Vibe Seller"), so the user reads
 * the agent's messages rather than its scratch work. Expanding shows the
 * steps as rows, each named by what it is for.
 */
function ActivityGroupView({ items, live = false }: ActivityGroupProps) {
  const { t } = useTranslation()
  const [expanded, setExpanded] = useState(false)
  const last = items[items.length - 1]

  let label: string
  let preview = ''
  if (live && last.type === 'thinking') {
    label = t('tasks.thinking')
    if (last.thinking?.isStreaming) {
      preview = last.thinking.content.trim().split('\n').slice(-1)[0]
    }
  } else if (live && last.toolCall) {
    label = liveLabel(describeStep(last.toolCall.tool, last.toolCall.input), t)
  } else {
    label = summarizeRun(items, t)
  }

  return (
    <div data-testid="activity-group">
      <button
        className="flex items-center gap-1 text-[14px] text-gray-500 hover:text-gray-800 max-w-full min-w-0 text-left"
        aria-expanded={expanded}
        onClick={() => setExpanded(e => !e)}
      >
        <span className={`truncate ${live ? 'animate-pulse' : ''}`}>{label}</span>
        <Chevron open={expanded} dir={expanded ? 'down' : 'right'} />
      </button>
      {preview && !expanded && (
        <div className="text-[12px] text-gray-400 mt-0.5 truncate">{preview}</div>
      )}
      {expanded && (
        <div className="mt-2 rounded-xl border border-gray-200 bg-white divide-y divide-gray-100">
          {buildRows(items).map(row =>
            row.kind === 'thought'
              ? <ThoughtRow key={row.item.id} item={row.item} />
              : <StepsRow key={row.items[0].id} row={row} />,
          )}
        </div>
      )}
    </div>
  )
}

// The run's array is rebuilt on every stream render; its items are not.
export const ActivityGroup = memo(
  ActivityGroupView,
  (a, b) =>
    a.live === b.live &&
    a.items.length === b.items.length &&
    a.items.every((item, i) => item === b.items[i]),
)
