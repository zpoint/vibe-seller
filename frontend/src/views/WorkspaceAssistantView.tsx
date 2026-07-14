import { useState, useRef, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import ReactMarkdown from 'react-markdown'
import type { AgentMessage } from '../types'

interface WorkspaceAssistantViewProps {
  messages: AgentMessage[]
  isRunning: boolean
  onSendMessage: (content: string) => void
  onStop: () => void
}

export function WorkspaceAssistantView({
  messages,
  isRunning,
  onSendMessage,
  onStop,
}: WorkspaceAssistantViewProps) {
  const { t } = useTranslation()
  const [debugMode, setDebugMode] = useState(false)
  const wsDebugInit = useRef(false)
  useEffect(() => { fetch('/api/auth/me', { credentials: 'include' }).then(r => r.json()).then(u => setDebugMode(u.debug_mode ?? false)).catch(() => {}) }, [])
  useEffect(() => { if (!wsDebugInit.current) { wsDebugInit.current = true; return } fetch('/api/auth/me/debug-mode', { method: 'PATCH', credentials: 'include', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ debug_mode: debugMode }) }).catch(() => {}) }, [debugMode])
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, isRunning])

  const handleSubmit = () => {
    const val = inputRef.current?.value.trim()
    if (!val) return
    onSendMessage(val)
    if (inputRef.current) inputRef.current.value = ''
  }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSubmit()
    }
  }

  const examples = [
    t('workspace.assistantExample1'),
    t('workspace.assistantExample2'),
    t('workspace.assistantExample3'),
  ]

  const hasMessages = messages.length > 0

  // Filter out result/tool_use/agent_event to avoid duplicate display
  const visibleMessages = debugMode
    ? messages.filter(m => m.role !== '_streaming')
    : messages.filter(m =>
        m.role !== 'result' && m.role !== 'tool_use' && m.role !== 'agent_event'
      )

  // Show thinking indicator when running and last visible message is from user
  const lastVisible = visibleMessages[visibleMessages.length - 1]
  const showThinking = isRunning && hasMessages && (!lastVisible || lastVisible.role === 'user')

  return (
    <div className="flex-1 flex flex-col bg-gray-50 relative">
      {/* Debug toggle — top-right floating */}
      {hasMessages && (
        <div className="absolute top-2 right-4 z-10">
          <button
            role="switch"
            aria-checked={debugMode}
            onClick={() => setDebugMode(!debugMode)}
            className="flex items-center gap-1.5 px-2 py-1 rounded-full bg-white/80 backdrop-blur border border-gray-200 shadow-sm hover:bg-gray-50 transition-colors"
          >
            <span className="text-[11px] text-gray-400">{t('tasks.debugMode')}</span>
            <div className={`relative w-7 h-3.5 rounded-full transition-colors ${debugMode ? 'bg-indigo-500' : 'bg-gray-300'}`}>
              <div className={`absolute top-0.5 left-0.5 w-2.5 h-2.5 bg-white rounded-full transition-transform pointer-events-none ${debugMode ? 'translate-x-3.5' : ''}`} />
            </div>
          </button>
        </div>
      )}

      {/* Messages area */}
      <div className="flex-1 overflow-y-auto p-4">
        {!hasMessages ? (
          <div className="flex flex-col items-center justify-center h-full gap-6">
            <div className="text-center">
              <div className="text-4xl opacity-30 mb-3">&#129302;</div>
              <p className="text-sm text-gray-500 max-w-md">{t('workspace.assistantWelcome')}</p>
            </div>
            <div className="flex flex-col gap-2 w-full max-w-md">
              {examples.map((ex, i) => (
                <button
                  key={i}
                  onClick={() => { if (inputRef.current) { inputRef.current.value = ex; inputRef.current.focus() } }}
                  className="text-left px-4 py-3 bg-white border border-gray-200 rounded-lg text-sm text-gray-700 hover:bg-indigo-50 hover:border-indigo-300 transition-colors"
                >
                  {ex}
                </button>
              ))}
            </div>
          </div>
        ) : debugMode ? (
          <div className="max-w-3xl mx-auto overflow-y-auto overflow-x-hidden bg-gray-900 rounded-lg border border-gray-700 p-3 space-y-1">
            {visibleMessages.map((msg, i) => (
              <div key={i} className="text-xs font-mono min-w-0">
                <span className={`inline-block px-1.5 py-0.5 rounded text-[10px] font-bold mr-1.5 ${
                  msg.role === 'assistant' || msg.role === 'result' ? 'bg-green-900 text-green-300' :
                  msg.role === 'user' ? 'bg-indigo-900 text-indigo-300' :
                  msg.role === 'tool_use' ? 'bg-indigo-900 text-indigo-300' :
                  msg.role === 'system' ? 'bg-red-900 text-red-300' :
                  msg.role === 'agent_event' ? 'bg-yellow-900 text-yellow-300' :
                  'bg-gray-700 text-gray-400'
                }`}>{msg.role}</span>
                <span className="text-gray-300 whitespace-pre-wrap break-all">{msg.content}</span>
              </div>
            ))}
            <div ref={messagesEndRef} />
          </div>
        ) : (
          <div className="space-y-4 max-w-3xl mx-auto">
            {visibleMessages.map((msg, i) => {
              if (msg.role === 'user') {
                return (
                  <div key={i} className="flex justify-end">
                    <div className="bg-indigo-500 text-white px-4 py-2 rounded-2xl rounded-br-md max-w-[80%] text-sm whitespace-pre-wrap">
                      {msg.content}
                    </div>
                  </div>
                )
              }
              // assistant, _streaming, system
              return (
                <div key={i} className="flex justify-start">
                  <div className={`px-4 py-2 rounded-2xl rounded-bl-md max-w-[80%] text-sm ${msg.role === 'system' ? 'bg-red-50 text-red-700' : 'bg-white border border-gray-200 text-gray-800'}`}>
                    {msg.role === '_streaming' ? (
                      <div className="prose prose-sm max-w-none">
                        <ReactMarkdown>{msg.content}</ReactMarkdown>
                        <span className="inline-block w-1.5 h-4 bg-indigo-400 animate-pulse ml-0.5 align-text-bottom" />
                      </div>
                    ) : (
                      <div className="prose prose-sm max-w-none">
                        <ReactMarkdown>{msg.content}</ReactMarkdown>
                      </div>
                    )}
                  </div>
                </div>
              )
            })}
            {showThinking && (
              <div className="flex justify-start">
                <div className="px-4 py-3 rounded-2xl rounded-bl-md bg-white border border-gray-200 text-sm text-gray-400 flex items-center gap-2">
                  <span className="flex gap-1">
                    <span className="w-1.5 h-1.5 bg-indigo-400 rounded-full animate-bounce" style={{ animationDelay: '0ms' }} />
                    <span className="w-1.5 h-1.5 bg-indigo-400 rounded-full animate-bounce" style={{ animationDelay: '150ms' }} />
                    <span className="w-1.5 h-1.5 bg-indigo-400 rounded-full animate-bounce" style={{ animationDelay: '300ms' }} />
                  </span>
                  {t('workspace.assistantThinking')}
                </div>
              </div>
            )}
            <div ref={messagesEndRef} />
          </div>
        )}
      </div>

      {/* Input bar */}
      <div className="border-t border-gray-200 bg-white px-4 py-3">
        <div className="flex gap-2 max-w-3xl mx-auto">
          <textarea
            ref={inputRef}
            onKeyDown={handleKeyDown}
            placeholder={t('workspace.assistantPlaceholder')}
            className="flex-1 px-4 py-2 border border-gray-300 rounded-xl text-sm resize-none focus:outline-none focus:ring-2 focus:ring-indigo-400 focus:border-transparent"
            rows={1}
            disabled={false}
          />
          {isRunning ? (
            <button
              onClick={onStop}
              className="px-4 py-2 bg-red-500 text-white rounded-xl text-sm font-medium hover:bg-red-600 transition-colors flex-shrink-0"
            >
              {t('workspace.assistantStop')}
            </button>
          ) : (
            <button
              onClick={handleSubmit}
              className="px-4 py-2 bg-indigo-500 text-white rounded-xl text-sm font-medium hover:bg-indigo-600 transition-colors flex-shrink-0"
            >
              {t('tasks.send')}
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
