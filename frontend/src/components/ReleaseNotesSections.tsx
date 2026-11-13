import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import Markdown from 'react-markdown'
import { REMARK_PLUGINS } from '../lib/markdownPlugins'
import type { ReleaseHighlight, ReleaseNoteItem } from '../types'

/**
 * The merged-notes envelope, as both endpoints that carry it answer:
 * `/api/system/update-check` (what is newer than this server) and
 * `/api/system/release-notes` (a range this server already crossed).
 * Only the fields this component reads are named, so neither caller has
 * to pretend to be the other one.
 */
export interface ReleaseNotesSource {
  current_version?: string | null
  latest_version?: string | null
  releases?: { version: string }[]
  releases_page_url?: string
  highlights?: ReleaseHighlight[]
  notes?: ReleaseNoteItem[]
}

const PROSE =
  'prose prose-sm max-w-none prose-p:my-0 prose-ul:my-1 prose-li:my-0.5 prose-code:text-xs prose-code:font-mono prose-code:text-gray-800 prose-code:bg-gray-100 prose-code:px-1 prose-code:py-0.5 prose-code:rounded prose-code:before:content-none prose-code:after:content-none prose-a:text-indigo-600'

const HIGHLIGHT_CAP = 6
const NOTE_CAP = 4

/** numeric-ish release-tag compare (0.2.1.devX compares by its
 * leading numbers — good enough to bound a notes list) */
const cmpVer = (a: string, b: string) => {
  const pa = a.split('.').map((n) => parseInt(n, 10) || 0)
  const pb = b.split('.').map((n) => parseInt(n, 10) || 0)
  for (let i = 0; i < Math.max(pa.length, pb.length); i++) {
    const d = (pa[i] ?? 0) - (pb[i] ?? 0)
    if (d) return d
  }
  return 0
}

/**
 * Release notes for every version between the installed one and the
 * target, merged BY SECTION (the server did the merge — one 亮点 list
 * and one 升级须知 list, newest version first, each item tagged with
 * its version). 亮点 collapses after 6 entries and 升级须知 after 4.
 *
 * `targetVersion` (the in-app flow's actual download target) bounds
 * the lists: on Windows the installer comes from GitHub Releases
 * while update-check's `latest_version` comes from PyPI, and in a
 * release window the two differ — the dialog must describe the
 * version the button will really apply, not the other one.
 */
export function ReleaseNotesSections({
  source,
  targetVersion,
}: {
  source: ReleaseNotesSource
  targetVersion?: string | null
}) {
  const { t, i18n } = useTranslation()
  const [showAllHl, setShowAllHl] = useState(false)
  const [showAllNotes, setShowAllNotes] = useState(false)
  const zh = (i18n.language || 'en').startsWith('zh')
  const pick = (item: { zh: string; en: string }) =>
    (zh ? item.zh || item.en : item.en || item.zh)
  // The server's items are markdown bullets ("- text"); the row layout
  // already IS the list item, so the outer marker is stripped (nested
  // sub-bullets keep theirs, like the mockup).
  const body = (t: string) => t.replace(/^- /, '')

  const within = (v: string) => !targetVersion || cmpVer(v, targetVersion) <= 0
  const highlights = (source.highlights ?? []).filter((h) => within(h.version))
  const notes = (source.notes ?? []).filter((n) => within(n.version))
  const versions = (source.releases ?? [])
    .map((r) => r.version)
    .filter(within)
  if (!highlights.length && !notes.length) {
    // Unfetched/absent notes must not leave an empty bordered band:
    // say so, and still offer the changelog link.
    return (
      <div className="space-y-2">
        <p className="text-sm text-gray-500">{t('updateCheck.noReleaseNotes')}</p>
        {source.releases_page_url && (
          <a
            href={source.releases_page_url}
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

  const shownHl = showAllHl ? highlights : highlights.slice(0, HIGHLIGHT_CAP)
  const shownNotes = showAllNotes ? notes : notes.slice(0, NOTE_CAP)

  return (
    <div className="space-y-4">
      {versions.length > 0 && source.current_version &&
        (targetVersion ?? source.latest_version) && (
          <p className="text-xs text-gray-500">
            {t('update.notesHead', {
              current: source.current_version,
              latest: targetVersion ?? source.latest_version,
              count: versions.length,
              versions: versions.map(v => `v${v}`).join(zh ? '、' : ', '),
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
                <span className="mt-0.5 shrink-0 self-start rounded bg-indigo-50 px-1 py-0.5 font-mono text-[10px] text-indigo-600">
                  v{item.version}
                </span>
                <div className={`min-w-0 flex-1 text-gray-600 ${PROSE}`}>
                  <Markdown remarkPlugins={REMARK_PLUGINS}>{body(pick(item))}</Markdown>
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
                <span className="mt-0.5 shrink-0 self-start rounded bg-gray-100 px-1 py-0.5 font-mono text-[10px] text-gray-500">
                  v{item.version}
                </span>
                <div className={`min-w-0 flex-1 text-gray-600 ${PROSE}`}>
                  <Markdown remarkPlugins={REMARK_PLUGINS}>{body(item.text)}</Markdown>
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

      {source.releases_page_url && (
        <a
          href={source.releases_page_url}
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
