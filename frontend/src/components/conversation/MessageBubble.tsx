import { memo } from 'react'
import { useTranslation } from 'react-i18next'
import Markdown from 'react-markdown'
import { parseAnswerBubble, type AnswerPair } from '../../lib/answerBubble'
import { REMARK_PLUGINS } from '../../lib/markdownPlugins'
import type { Components } from 'react-markdown'
import { parseUserContent } from '../../lib/userContent'
import { AttachmentThumbs } from './AttachmentThumbs'
import type { SettingsTab } from '../../views/SettingsView'
import { MarkdownLink } from './MarkdownLink'

// Primary agent voice: 16px prose with relaxed leading — the focal
// point of the stream. Secondary chrome (tools/thinking) stays small
// and dimmed so this reads as the loudest layer.
const PROSE = 'prose prose-neutral max-w-none text-[17px] leading-[1.7] prose-p:my-2.5 prose-p:leading-[1.7] prose-p:text-gray-800 prose-headings:font-semibold prose-headings:text-gray-900 prose-h1:text-[19px] prose-h1:mb-2 prose-h1:mt-4 prose-h2:text-[17px] prose-h2:mb-2 prose-h2:mt-5 prose-ul:my-2.5 prose-ul:pl-6 prose-li:my-1 prose-li:marker:text-gray-400 prose-code:text-[14px] prose-code:font-mono prose-code:text-gray-800 prose-code:bg-gray-100 prose-code:px-1 prose-code:py-0.5 prose-code:rounded prose-code:before:content-none prose-code:after:content-none prose-pre:bg-gray-50 prose-pre:text-gray-800 prose-pre:border prose-pre:border-gray-200 prose-pre:rounded prose-pre:p-3 prose-pre:my-3 prose-pre:text-[14px] prose-a:text-indigo-600'


interface MessageBubbleProps {
  role: string
  content: string
  // Open a Settings tab. When provided, a SETTINGS_LINKS link renders
  // as an inline CTA button.
  onOpenSettings?: (tab: SettingsTab, anchor?: string) => void
}

/** The user's answers to the agent's questions: each question, then the answer. */
function AnswerBubble({ pairs }: { pairs: AnswerPair[] }) {
  const { t } = useTranslation()
  return (
    <div className="flex justify-end" data-answer-bubble>
      <div className="ph-sensitive max-w-[80%] bg-gray-100 rounded-2xl rounded-br-md px-4 py-3 text-gray-800 divide-y divide-gray-200">
        {pairs.map((p, i) => (
          <div key={i} className={i === 0 ? 'pb-3 last:pb-0' : 'py-3 last:pb-0'}>
            <div className="text-[12px] font-medium text-gray-400">{t('tasks.answerBubbleQuestion')}</div>
            <div className="mt-0.5 text-[14px] leading-relaxed text-gray-500 whitespace-pre-wrap">{p.question}</div>
            <div className="mt-2 text-[12px] font-medium text-gray-400">{t('tasks.answerBubbleAnswer')}</div>
            <div className="mt-0.5 text-[16px] leading-relaxed font-medium text-gray-900 whitespace-pre-wrap">{p.answer}</div>
          </div>
        ))}
      </div>
    </div>
  )
}

function MessageBubbleView({ role, content, onOpenSettings }: MessageBubbleProps) {
  if (role === 'user' || role === 'answer') {
    const pairs = parseAnswerBubble(content)
    if (pairs) return <AnswerBubble pairs={pairs} />
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
    a: (props) => <MarkdownLink {...props} onOpenSettings={onOpenSettings} />,
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
