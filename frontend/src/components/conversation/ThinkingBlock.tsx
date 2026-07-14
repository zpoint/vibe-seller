import { useState } from 'react'
import { useTranslation } from 'react-i18next'

interface ThinkingBlockProps {
  content: string
  isStreaming: boolean
}

export function ThinkingBlock({ content, isStreaming }: ThinkingBlockProps) {
  const { t } = useTranslation()
  const [expanded, setExpanded] = useState(false)

  if (isStreaming) {
    // Show last ~3 lines while streaming
    const lines = content.split('\n')
    const preview = lines.slice(-3).join('\n')
    return (
      <div className="py-0.5">
        <div className="flex items-center gap-1.5 text-xs text-purple-500">
          <span className="animate-pulse">💭</span>
          <span>{t('tasks.thinking', 'Thinking...')}</span>
        </div>
        {preview && (
          <div className="text-[11px] text-gray-400 mt-0.5 ml-5 whitespace-pre-wrap line-clamp-3">
            {preview}
          </div>
        )}
      </div>
    )
  }

  // Collapsed state — click to expand
  return (
    <div className="py-0.5">
      <button
        className="flex items-center gap-1.5 text-xs text-gray-400 hover:text-purple-500"
        onClick={() => setExpanded(e => !e)}
      >
        <span>💭</span>
        <span>{t('tasks.thinking', 'Thinking...')}</span>
        <span className={`text-[10px] transition-transform ${expanded ? 'rotate-90' : ''}`}>
          ▶
        </span>
      </button>
      {expanded && (
        <div className="text-[11px] text-gray-400 mt-1 ml-5 whitespace-pre-wrap max-h-48 overflow-y-auto">
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
      <span className="text-xs text-gray-400">
        {t('tasks.agentWorking', 'Agent is working...')}
      </span>
    </div>
  )
}
