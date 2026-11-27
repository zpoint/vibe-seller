import { useTranslation } from 'react-i18next'
import { docsHelpUrl, type DocsHelpTopic } from '../../lib/docsHelp'
import type { VendorDocsLink } from '../../types'

/**
 * "How to fix this ↗": opens the docs section for a failure the page is
 * showing, in the UI's language. Every in-app error that the docs
 * explain carries one, next to the error text. Links come either from
 * the shared topic table (lib/docsHelp.ts) or — for browser vendors —
 * from the vendor's spec (``urls``), so a vendor's docs links are
 * declared with the vendor, not in a shared map.
 */
export function DocsHelpLink({ topic, urls, className = '' }: {
  topic?: DocsHelpTopic
  urls?: VendorDocsLink
  className?: string
}) {
  const { t, i18n } = useTranslation()
  const zh = (i18n.language || 'en').startsWith('zh')
  const href = urls ? (zh ? urls.zh : urls.en)
    : topic ? docsHelpUrl(topic, i18n.language)
    : ''
  if (!href) return null
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      data-testid="docs-help-link"
      data-topic={urls ? urls.topic : topic}
      className={`inline-flex shrink-0 items-center gap-0.5 whitespace-nowrap text-xs font-medium text-indigo-600 underline underline-offset-2 hover:text-indigo-800 ${className}`}
    >
      {t('common.docsHelp')}
      <svg aria-hidden="true" className="w-3 h-3" viewBox="0 0 20 20" fill="currentColor">
        <path d="M11 3a1 1 0 100 2h2.586l-6.293 6.293a1 1 0 101.414 1.414L15 6.414V9a1 1 0 102 0V4a1 1 0 00-1-1h-5z" />
        <path d="M5 5a2 2 0 00-2 2v8a2 2 0 002 2h8a2 2 0 002-2v-3a1 1 0 10-2 0v3H5V7h3a1 1 0 000-2H5z" />
      </svg>
    </a>
  )
}
