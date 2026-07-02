import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { UpdateCheckResult } from '../types'

type Props = {
  result: UpdateCheckResult
  onClose: () => void
}

const PROSE = 'prose prose-sm max-w-none prose-p:my-1 prose-ul:my-1 prose-li:my-0.5 prose-code:text-xs prose-code:font-mono prose-code:text-gray-800 prose-code:bg-gray-100 prose-code:px-1 prose-code:py-0.5 prose-code:rounded prose-code:before:content-none prose-code:after:content-none'

export function UpdateAvailableModal({ result, onClose }: Props) {
  const { t } = useTranslation()
  const [copied, setCopied] = useState(false)

  const copyCommand = async () => {
    if (!result.upgrade_command) return
    try {
      await navigator.clipboard.writeText(result.upgrade_command)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch {
      /* clipboard blocked — user can still select+copy manually */
    }
  }

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="update-available-title"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
    >
      <div className="w-full max-w-lg max-h-[85vh] flex flex-col rounded-lg bg-white shadow-xl">
        <div className="p-6 pb-4 border-b border-gray-100">
          <h2 id="update-available-title" className="text-lg font-semibold text-gray-900">
            {t('updateCheck.title')}
          </h2>
          <p className="mt-1 text-sm text-gray-600">
            {t('updateCheck.versionLine', {
              current: result.current_version,
              latest: result.latest_version,
            })}
          </p>

          {result.upgrade_command ? (
            <div className="mt-4">
              <p className="text-sm text-gray-700">{t('updateCheck.macLinuxInstructions')}</p>
              <div className="mt-2 flex items-center gap-2">
                <pre className="flex-1 overflow-auto rounded bg-gray-100 p-2.5 text-xs font-mono text-gray-900">
                  {result.upgrade_command}
                </pre>
                <button
                  onClick={copyCommand}
                  className="shrink-0 rounded border border-gray-300 px-3 py-1.5 text-xs font-medium text-gray-700 hover:bg-gray-50"
                >
                  {copied ? t('updateCheck.copied') : t('updateCheck.copyCommand')}
                </button>
              </div>
            </div>
          ) : result.download_url ? (
            <div className="mt-4">
              <p className="text-sm text-gray-700">{t('updateCheck.windowsInstructions')}</p>
              <a
                href={result.download_url}
                target="_blank"
                rel="noopener noreferrer"
                className="mt-2 inline-block rounded bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700"
              >
                {t('updateCheck.downloadButton')}
              </a>
            </div>
          ) : null}
        </div>

        {result.releases_page_url && (
          <div className="overflow-y-auto p-6 pt-4 space-y-4">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-gray-500">
              {t('updateCheck.whatsNew')}
            </h3>
            {result.releases && result.releases.length > 0 ? (
              result.releases.map(rel => (
                <div key={rel.version} className="border-l-2 border-indigo-200 pl-3">
                  <div className="text-sm font-medium text-gray-800">v{rel.version}</div>
                  <div className={`text-gray-600 ${PROSE}`}>
                    <Markdown remarkPlugins={[remarkGfm]}>{rel.body || t('updateCheck.noReleaseNotes')}</Markdown>
                  </div>
                </div>
              ))
            ) : (
              <p className="text-sm text-gray-500">{t('updateCheck.noReleaseNotes')}</p>
            )}
            <a
              href={result.releases_page_url}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-block text-sm text-indigo-600 hover:underline"
            >
              {t('updateCheck.viewFullChangelog')}
            </a>
          </div>
        )}

        <div className="p-4 border-t border-gray-100 flex justify-end">
          <button
            onClick={onClose}
            className="rounded border border-gray-300 px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-50"
          >
            {t('updateCheck.remindLater')}
          </button>
        </div>
      </div>
    </div>
  )
}
