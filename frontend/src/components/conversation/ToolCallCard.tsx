import { useState } from 'react'
import { useTranslation } from 'react-i18next'

interface ToolCallProps {
  tool: string
  input?: Record<string, unknown>
}

function toolSummary(tool: string, input?: Record<string, unknown>): string {
  if (!input) return ''
  if (tool === 'Read' || tool === 'Edit' || tool === 'Write') {
    const filePath = input['file_path']
    return typeof filePath === 'string' ? filePath : ''
  }
  if (tool === 'Grep' || tool === 'Glob') {
    const pattern = input['pattern']
    return typeof pattern === 'string' ? pattern : ''
  }
  if (tool === 'Bash') {
    const command = input['command']
    const cmd = typeof command === 'string' ? command : ''
    return cmd.length > 60 ? cmd.slice(0, 60) + '…' : cmd
  }
  return ''
}

function toolIcon(tool: string): string {
  if (tool === 'Read') return '📄'
  if (tool === 'Edit' || tool === 'Write') return '✏️'
  if (tool === 'Grep' || tool === 'Glob') return '🔍'
  if (tool === 'Bash') return '🔧'
  return '⚙️'
}

export function ToolCallLine({ tool, input }: ToolCallProps) {
  const [expanded, setExpanded] = useState(false)
  const summary = toolSummary(tool, input)

  return (
    <div>
      <button
        className="flex items-center gap-1.5 text-[13px] text-gray-500 hover:text-gray-700 w-full text-left py-0.5"
        onClick={() => setExpanded(e => !e)}
      >
        <span>{toolIcon(tool)}</span>
        <span className="font-medium text-gray-600">{tool}</span>
        {summary && (
          <span className="text-gray-400 truncate">{summary}</span>
        )}
      </button>
      {expanded && input && (
        <pre className="text-[12px] text-gray-400 bg-gray-50 rounded p-1.5 mt-0.5 ml-5 overflow-x-auto">
          {JSON.stringify(input, null, 2)}
        </pre>
      )}
    </div>
  )
}

interface ToolCallGroupProps {
  items: ToolCallProps[]
}

export function ToolCallGroup({ items }: ToolCallGroupProps) {
  const { t } = useTranslation()
  const [expanded, setExpanded] = useState(false)

  if (items.length === 1) {
    return (
      <div className="py-0.5">
        <ToolCallLine {...items[0]} />
      </div>
    )
  }

  return (
    <div className="py-0.5">
      <button
        className="flex items-center gap-1.5 text-[13px] text-gray-500 hover:text-gray-700"
        onClick={() => setExpanded(e => !e)}
      >
        <span className={`transition-transform ${expanded ? 'rotate-90' : ''}`}>
          ▶
        </span>
        <span>
          {items.length === 1
            ? t('tasks.toolCall')
            : t('tasks.toolCalls', { count: items.length })}
        </span>
      </button>
      {expanded && (
        <div className="mt-1 space-y-0.5 ml-3">
          {items.map((item, i) => (
            <ToolCallLine key={i} {...item} />
          ))}
        </div>
      )}
    </div>
  )
}
