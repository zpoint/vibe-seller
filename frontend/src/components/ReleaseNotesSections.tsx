import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { UpdateCheckResult } from '../types'

const PROSE =
  'prose prose-sm max-w-none prose-p:my-0 prose-ul:my-1 prose-li:my-0.5 prose-code:text-xs prose-code:font-mono prose-code:text-gray-800 prose-code:bg-gray-100 prose-code:px-1 prose-code:py-0.5 prose-code:rounded prose-code:before:content-none prose-code:after:content-none prose-a:text-indigo-600'

const HIGHLIGHT_CAP = 6
const NOTE_CAP = 4

/**
 * Release notes for every version between the installed one and the
 * target, merged BY SECTION (the server did the merge — one 亮点 list
 * and one 升级须知 list, newest version first, each item tagged with
 * its version). 亮点 collapses after 6 entries and 升级须知 after 4.
 */
export function ReleaseNotesSections({ check }: { check: UpdateCheckResult }) {
  const { t, i18n } = useTranslation()
  const [showAllHl, setShowAllHl] = useState(false)
  const [showAllNotes, setShowAllNotes] = useState(false)
  const zh = (i18n.language || 'en').startsWith('zh')
  const pick = (item: { zh: string; en: string }) =>
    (zh ? item.zh || item.en : item.en || item.zh)

  const highlights = check.highlights ?? []
  const notes = check.notes ?? []
  const versions = (check.releases ?? []).map(r => r.version)
  if (!highlights.length && !notes.length) return null

  const shownHl = showAllHl ? highlights : highlights.slice(0, HIGHLIGHT_CAP)
  const shownNotes = showAllNotes ? notes : notes.slice(0, NOTE_CAP)

  return (
    <div className="space-y-4">
      {check.current_version && check.latest_version && versions.length > 0 && (
        <p className="text-xs text-gray-500">
          {t('update.notesHead', {
            current: check.current_version,
            latest: check.latest_version,
            count: versions.length,
            versions: versions.map(v => `v${v}`).join('、'),
          })}
        </p>
      )}

      {highlights.length > 0 && (
        <section>
          <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-400">
            {t('update.highlights')}
          </h3>
          <ul className="space-y-2.5">
            {shownHl.map((item, i) => (
              <li key={`h${item.version}-${i}`} className="flex gap-2">
                <span className="mt-0.5 shrink-0 rounded bg-indigo-50 px-1 py-0.5 font-mono text-[10px] text-indigo-600">
                  v{item.version}
                </span>
                <div className={`min-w-0 flex-1 text-gray-600 ${PROSE}`}>
                  <Markdown remarkPlugins={[remarkGfm]}>{pick(item)}</Markdown>
                </div>
              </li>
            ))}
          </ul>
          {highlights.length > shownHl.length && (
            <button
              type="button"
              onClick={() => setShowAllHl(true)}
              className="mt-2 text-xs font-medium text-indigo-600 hover:underline"
            >
              {t('update.expandRest', { count: highlights.length - shownHl.length })}
            </button>
          )}
        </section>
      )}

      {notes.length > 0 && (
        <section>
          <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-400">
            {t('update.upgradeNotes')}
          </h3>
          <ul className="space-y-2.5">
            {shownNotes.map((item, i) => (
              <li key={`n${item.version}-${i}`} className="flex gap-2">
                <span className="mt-0.5 shrink-0 rounded bg-gray-100 px-1 py-0.5 font-mono text-[10px] text-gray-500">
                  v{item.version}
                </span>
                <div className={`min-w-0 flex-1 text-gray-600 ${PROSE}`}>
                  <Markdown remarkPlugins={[remarkGfm]}>{item.text}</Markdown>
                </div>
              </li>
            ))}
          </ul>
          {notes.length > shownNotes.length && (
            <button
              type="button"
              onClick={() => setShowAllNotes(true)}
              className="mt-2 text-xs font-medium text-indigo-600 hover:underline"
            >
              {t('update.expandRest', { count: notes.length - shownNotes.length })}
            </button>
          )}
        </section>
      )}

      {check.releases_page_url && (
        <a
          href={check.releases_page_url}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-block text-sm text-indigo-600 hover:underline"
        >
          {t('update.viewFullChangelog')}
        </a>
      )}
    </div>
  )
}
