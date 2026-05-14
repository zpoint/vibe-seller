import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

const PROSE = 'prose prose-sm max-w-none prose-code:text-xs prose-code:font-mono prose-code:text-gray-800 prose-code:bg-gray-100 prose-code:px-1 prose-code:py-0.5 prose-code:rounded prose-code:before:content-none prose-code:after:content-none prose-pre:bg-gray-50 prose-pre:text-gray-800 prose-pre:border prose-pre:border-gray-200 prose-pre:rounded prose-pre:p-2 prose-pre:my-2 prose-h1:text-sm prose-h1:font-semibold prose-h1:text-gray-800 prose-h1:mb-2 prose-h2:text-sm prose-h2:font-medium prose-h2:text-gray-700 prose-h2:mb-1.5 prose-p:my-1.5 prose-p:text-gray-700 prose-p:leading-relaxed prose-ul:my-1.5 prose-ul:pl-4 prose-li:my-0.5 prose-li:marker:text-gray-400'

interface MessageBubbleProps {
  role: string
  content: string
}

export function MessageBubble({ role, content }: MessageBubbleProps) {
  if (role === 'user') {
    return (
      <div className="flex justify-end">
        <div className="max-w-[80%] bg-indigo-50 rounded-2xl rounded-br-sm px-4 py-2 text-sm text-gray-800">
          {content}
        </div>
      </div>
    )
  }

  const isStreaming = role === '_streaming'
  return (
    <div className="flex justify-start">
      <div className={`max-w-[90%] ${isStreaming ? 'bg-white border border-blue-100' : 'bg-white border border-gray-100'} rounded-2xl rounded-bl-sm px-4 py-2`}>
        <div className={`text-sm text-gray-800 ${PROSE}`}>
          <Markdown remarkPlugins={[remarkGfm]}>{content}</Markdown>
        </div>
        {isStreaming && (
          <span className="inline-block w-1.5 h-4 bg-blue-400 animate-pulse rounded-sm ml-0.5 align-text-bottom" />
        )}
      </div>
    </div>
  )
}
