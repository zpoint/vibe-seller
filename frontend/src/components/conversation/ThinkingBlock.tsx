import { memo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { thoughtLine } from '../../lib/activity'
import { Chevron } from './ToolCallCard'

interface ThinkingBlockProps {
  content: string
  isStreaming: boolean
}

function ThinkingBlockView({ content, isStreaming }: ThinkingBlockProps) {
  const { t } = useTranslation()
  const [expanded, setExpanded] = useState(false)

  if (isStreaming) {
    // Show last ~3 lines while streaming
    const lines = content.split('\n')
    const preview = lines.slice(-3).join('\n')
    return (
      <div className="py-0.5">
        <div className="flex items-center gap-1.5 text-[13px] text-indigo-500">
          <span className="animate-pulse">💭</span>
          <span>{t('tasks.thinking', 'Thinking...')}</span>
        </div>
        {preview && (
          <div className="text-[12px] text-gray-400 mt-0.5 ml-5 whitespace-pre-wrap line-clamp-3">
            {preview}
          </div>
        )}
      </div>
    )
  }

  // Finished: the thought's first line as the agent's narration, the same
  // as a thought row inside a folded run. "Thinking..." here would claim
  // the agent is still at it on a task that ended long ago.
  return (
    <div className="py-0.5">
      <button
        className="flex items-center gap-1 text-[13px] italic text-gray-400 hover:text-gray-600 max-w-full min-w-0 text-left"
        aria-expanded={expanded}
        onClick={() => setExpanded(e => !e)}
      >
        <span className="truncate">{thoughtLine(content) || t('activity.thought_one')}</span>
        <Chevron open={expanded} />
      </button>
      {expanded && (
        <div className="text-[13px] leading-[1.6] text-gray-500 mt-1 whitespace-pre-wrap max-h-64 overflow-y-auto">
          {content}
        </div>
      )}
    </div>
  )
}

export function WorkingIndicator() {
  const { t } = useTranslation()
  return (
    <div className="flex items-center gap-2 py-2 px-1">
      <div className="flex gap-0.5">
        <span className="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '0ms' }} />
        <span className="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '150ms' }} />
        <span className="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '300ms' }} />
      </div>
      <span className="text-[13px] text-gray-400">
        {t('tasks.agentWorking', 'Agent is working...')}
      </span>
    </div>
  )
}

export const ThinkingBlock = memo(ThinkingBlockView)
