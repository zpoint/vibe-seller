import { useEffect, useMemo, useState } from 'react'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { useTranslation } from 'react-i18next'
import { read, utils } from 'xlsx'
import { rawUrl } from '../lib/fileKind'

const PROSE = 'prose prose-sm max-w-none prose-code:text-xs prose-code:font-mono prose-code:text-gray-800 prose-code:bg-gray-100 prose-code:px-1 prose-code:py-0.5 prose-code:rounded prose-code:before:content-none prose-code:after:content-none prose-pre:bg-gray-50 prose-pre:text-gray-800 prose-pre:border prose-pre:border-gray-200 prose-pre:rounded prose-pre:p-2 prose-pre:my-2 prose-table:text-xs prose-th:px-2 prose-th:py-1 prose-td:px-2 prose-td:py-1'

export function MarkdownView({ content }: { content: string }) {
  return (
    <div className="flex-1 overflow-y-auto bg-white">
      <div className={`${PROSE} p-6`}>
        <Markdown remarkPlugins={[remarkGfm]}>{content}</Markdown>
      </div>
    </div>
  )
}

const MAX_ROWS = 500

function parseDelimited(content: string, delim: string): string[][] {
  if (delim === '\t') {
    return content.split(/\r?\n/).filter(l => l.length).map(l => l.split('\t'))
  }
  // minimal quote-aware csv
  const rows: string[][] = []
  let row: string[] = [], cell = '', inQ = false
  for (let i = 0; i < content.length; i++) {
    const c = content[i]
    if (inQ) {
      if (c === '"') { if (content[i + 1] === '"') { cell += '"'; i++ } else inQ = false }
      else cell += c
    } else if (c === '"') inQ = true
    else if (c === ',') { row.push(cell); cell = '' }
    else if (c === '\n' || c === '\r') {
      if (c === '\r' && content[i + 1] === '\n') i++
      row.push(cell); cell = ''
      if (row.length > 1 || row[0] !== '') rows.push(row)
      row = []
    } else cell += c
  }
  if (cell !== '' || row.length) { row.push(cell); rows.push(row) }
  return rows
}

export function DelimitedTable({ content, path }: { content: string; path: string }) {
  const { t } = useTranslation()
  const delim = path.toLowerCase().endsWith('.tsv') ? '\t' : ','
  const rows = useMemo(() => parseDelimited(content, delim), [content, delim])
  const shown = rows.slice(0, MAX_ROWS)
  const [head, ...body] = shown
  return (
    <div className="flex-1 overflow-auto bg-white">
      {rows.length > MAX_ROWS && (
        <p className="px-4 py-1.5 text-[11px] text-amber-600 bg-amber-50 border-b border-amber-100">
          {t('workspace.truncatedRows', { shown: MAX_ROWS, total: rows.length })}
        </p>
      )}
      <table className="text-xs border-collapse">
        <thead className="sticky top-0">
          <tr>{(head ?? []).map((c, i) => (
            <th key={i} className="px-2 py-1 bg-gray-100 border border-gray-200 text-left font-medium text-gray-700 whitespace-nowrap">{c}</th>
          ))}</tr>
        </thead>
        <tbody>
          {body.map((r, ri) => (
            <tr key={ri} className="odd:bg-white even:bg-gray-50">
              {r.map((c, ci) => (
                <td key={ci} className="px-2 py-1 border border-gray-100 text-gray-600 whitespace-nowrap max-w-[28rem] overflow-hidden text-ellipsis">{c}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export function SpreadsheetViewer({ path }: { path: string }) {
  const { t } = useTranslation()
  const [sheets, setSheets] = useState<string[]>([])
  const [active, setActive] = useState(0)
  const [error, setError] = useState('')
  const [wb, setWb] = useState<ReturnType<typeof read> | null>(null)

  useEffect(() => {
    setWb(null); setSheets([]); setActive(0); setError('')
    ;(async () => {
      try {
        const res = await fetch(rawUrl(path), { credentials: 'same-origin' })
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        const ab = await res.arrayBuffer()
        const book = read(ab, { sheetRows: 300 })
        setWb(book)
        setSheets(book.SheetNames)
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e))
      }
    })()
  }, [path])

  // Cell values render through React (no innerHTML) — spreadsheet
  // content is untrusted, so we never let it shape markup.
  const rows = useMemo<unknown[][]>(() => {
    if (!wb || !sheets.length) return []
    const ws = wb.Sheets[sheets[active]]
    return ws ? utils.sheet_to_json(ws, { header: 1 }) as unknown[][] : []
  }, [wb, sheets, active])

  if (error) return <div className="flex-1 flex items-center justify-center text-sm text-red-500">{error}</div>
  return (
    <div className="flex-1 flex flex-col overflow-hidden bg-white">
      <div className="flex items-center gap-1 px-3 py-1.5 border-b border-gray-200 bg-gray-50 overflow-x-auto">
        {sheets.map((n, i) => (
          <button key={n} onClick={() => setActive(i)}
            className={`px-2.5 py-1 text-xs rounded ${i === active ? 'bg-white shadow text-gray-900 font-medium' : 'text-gray-500 hover:text-gray-700'}`}>
            {n}
          </button>
        ))}
        <span className="ml-auto text-[10px] text-gray-400 flex-shrink-0">{t('workspace.viewerReadOnly')}</span>
      </div>
      <div className="flex-1 overflow-auto p-2">
        <table className="text-xs border-collapse">
          <tbody>
            {rows.map((r, ri) => (
              <tr key={ri} className="odd:bg-white even:bg-gray-50">
                {(r as unknown[]).map((c, ci) => (
                  <td key={ci} className="px-2 py-0.5 border border-gray-200 text-gray-600 whitespace-nowrap max-w-[28rem] overflow-hidden text-ellipsis">
                    {c == null ? '' : String(c)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

export function PdfViewer({ path }: { path: string }) {
  return <iframe title={path} src={rawUrl(path)} className="flex-1 w-full border-0 bg-gray-100" />
}

export function ImageViewer({ path }: { path: string }) {
  return (
    <div className="flex-1 overflow-auto flex items-center justify-center bg-gray-100 p-4">
      <img src={rawUrl(path)} alt={path} className="max-w-full max-h-full shadow" />
    </div>
  )
}

export function BinaryDownload({ path }: { path: string }) {
  const { t } = useTranslation()
  return (
    <div className="flex-1 flex flex-col items-center justify-center gap-3 text-gray-400">
      <div className="text-4xl opacity-40">&#128230;</div>
      <a href={rawUrl(path)} download className="px-4 py-2 text-xs font-medium bg-blue-600 text-white rounded-lg hover:bg-blue-700">
        {t('workspace.download')} {path.split('/').pop()}
      </a>
    </div>
  )
}
