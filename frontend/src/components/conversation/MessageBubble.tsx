import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

// Primary agent voice: 16px prose with relaxed leading — the focal
// point of the stream. Secondary chrome (tools/thinking) stays small
// and dimmed so this reads as the loudest layer.
const PROSE = 'prose prose-neutral max-w-none text-[17px] leading-[1.7] prose-p:my-2.5 prose-p:leading-[1.7] prose-p:text-gray-800 prose-headings:font-semibold prose-headings:text-gray-900 prose-h1:text-[19px] prose-h1:mb-2 prose-h1:mt-4 prose-h2:text-[17px] prose-h2:mb-2 prose-h2:mt-5 prose-ul:my-2.5 prose-ul:pl-6 prose-li:my-1 prose-li:marker:text-gray-400 prose-code:text-[14px] prose-code:font-mono prose-code:text-gray-800 prose-code:bg-gray-100 prose-code:px-1 prose-code:py-0.5 prose-code:rounded prose-code:before:content-none prose-code:after:content-none prose-pre:bg-gray-50 prose-pre:text-gray-800 prose-pre:border prose-pre:border-gray-200 prose-pre:rounded prose-pre:p-3 prose-pre:my-3 prose-pre:text-[14px] prose-a:text-indigo-600'

interface MessageBubbleProps {
  role: string
  content: string
}

export function MessageBubble({ role, content }: MessageBubbleProps) {
  if (role === 'user') {
    return (
      <div className="flex justify-end">
        <div className="max-w-[80%] bg-gray-100 rounded-2xl rounded-br-md px-4 py-3 text-[16px] leading-relaxed text-gray-800 whitespace-pre-wrap">
          {content}
        </div>
      </div>
    )
  }

  const isStreaming = role === '_streaming'
  return (
    <div className="min-w-0">
      <div className={`text-gray-800 ${PROSE}`}>
        <Markdown remarkPlugins={[remarkGfm]}>{content}</Markdown>
      </div>
      {isStreaming && (
        <span className="inline-block w-1.5 h-[18px] bg-indigo-500 animate-pulse rounded-sm ml-0.5 align-text-bottom" />
      )}
    </div>
  )
}
