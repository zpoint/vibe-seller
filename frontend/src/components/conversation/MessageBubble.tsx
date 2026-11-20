import { memo } from 'react'
import Markdown from 'react-markdown'
import { REMARK_PLUGINS } from '../../lib/markdownPlugins'
import type { Components } from 'react-markdown'
import { parseUserContent } from '../../lib/userContent'
import { AttachmentThumbs } from './AttachmentThumbs'

// Primary agent voice: 16px prose with relaxed leading — the focal
// point of the stream. Secondary chrome (tools/thinking) stays small
// and dimmed so this reads as the loudest layer.
const PROSE = 'prose prose-neutral max-w-none text-[17px] leading-[1.7] prose-p:my-2.5 prose-p:leading-[1.7] prose-p:text-gray-800 prose-headings:font-semibold prose-headings:text-gray-900 prose-h1:text-[19px] prose-h1:mb-2 prose-h1:mt-4 prose-h2:text-[17px] prose-h2:mb-2 prose-h2:mt-5 prose-ul:my-2.5 prose-ul:pl-6 prose-li:my-1 prose-li:marker:text-gray-400 prose-code:text-[14px] prose-code:font-mono prose-code:text-gray-800 prose-code:bg-gray-100 prose-code:px-1 prose-code:py-0.5 prose-code:rounded prose-code:before:content-none prose-code:after:content-none prose-pre:bg-gray-50 prose-pre:text-gray-800 prose-pre:border prose-pre:border-gray-200 prose-pre:rounded prose-pre:p-3 prose-pre:my-3 prose-pre:text-[14px] prose-a:text-indigo-600'

// Stable target the base-prompt breadcrumb emits when image generation
// isn't configured. The agent may translate the link TEXT, but keeps
// this href — so we render an in-app CTA regardless of language instead
// of a dead #anchor. See VISION_SETUP_BREADCRUMB in app/task_runner.py.
const VISION_SETUP_HREF = '#vision-setup'

interface MessageBubbleProps {
  role: string
  content: string
  // Navigate to Settings → AI → Vision. When provided, the breadcrumb
  // link renders as an inline CTA button.
  onOpenVisionSetup?: () => void
}

function MessageBubbleView({ role, content, onOpenVisionSetup }: MessageBubbleProps) {
  if (role === 'user') {
    const { text, images } = parseUserContent(content)
    return (
      <div className="flex justify-end">
        <div className="ph-sensitive max-w-[80%] bg-gray-100 rounded-2xl rounded-br-md px-4 py-3 text-[16px] leading-relaxed text-gray-800">
          {text && <div className="whitespace-pre-wrap">{text}</div>}
          <AttachmentThumbs images={images} className={`justify-end ${text ? 'mt-2' : ''}`} />
        </div>
      </div>
    )
  }

  const isStreaming = role === '_streaming'

  const components: Components = {
    a({ href, children, ...props }) {
      if (href === VISION_SETUP_HREF && onOpenVisionSetup) {
        return (
          <button
            type="button"
            onClick={onOpenVisionSetup}
            className="not-prose inline-flex items-center gap-1.5 rounded-lg border border-indigo-300 bg-indigo-50 px-2.5 py-1 text-[14px] font-medium text-indigo-700 hover:bg-indigo-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-500"
          >
            {children}
            <span aria-hidden="true">→</span>
          </button>
        )
      }
      return <a href={href} {...props}>{children}</a>
    },
  }

  return (
    <div className="min-w-0">
      <div className={`ph-sensitive text-gray-800 ${PROSE}`}>
        <Markdown remarkPlugins={REMARK_PLUGINS} components={components}>{content}</Markdown>
      </div>
      {isStreaming && (
        <span className="inline-block w-1.5 h-[18px] bg-indigo-500 animate-pulse rounded-sm ml-0.5 align-text-bottom" />
      )}
    </div>
  )
}

// Re-render only when the message itself changes. The vision-setup
// callback is a fixed action recreated inline on every app render, so
// comparing it would defeat the memo (typing lagged on long tasks).
export const MessageBubble = memo(
  MessageBubbleView,
  (a, b) => a.role === b.role && a.content === b.content,
)
