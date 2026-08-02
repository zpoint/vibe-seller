import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { useTranslation } from 'react-i18next'

const PROSE_RESULT =
  'prose prose-sm max-w-none prose-headings:font-semibold prose-p:my-2'

/**
 * A task result rendered as plain markdown.
 *
 * Shared so the audit card can fall back to it: a decision console with
 * no rows is worse than no console, because "0 campaigns" reads as a
 * finding rather than as a report the parser could not read.
 */
export function PlainResult({ report }: { report: string }) {
  const { t } = useTranslation()
  return (
    <div className="bg-white rounded-2xl border border-gray-200 shadow-sm p-5 sm:p-6">
      <div className="flex items-center gap-2 mb-3">
        <span className="w-1.5 h-1.5 rounded-full bg-green-500" />
        <span className="text-[11px] font-semibold uppercase tracking-[0.08em] text-green-600">
          {t('tasks.result')}
        </span>
      </div>
      <div className={`text-gray-800 ${PROSE_RESULT} overflow-x-auto`}>
        <Markdown remarkPlugins={[remarkGfm]}>{report || ''}</Markdown>
      </div>
    </div>
  )
}
