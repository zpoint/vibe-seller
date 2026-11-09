import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { TaskUsage } from '../types'
import {
  type Currency,
  USD_TO_CNY,
  contextPercent,
  currencyFor,
  formatCost,
  formatTokens,
} from '../lib/usageFormat'

/** The cost label — chip: "$1.23" / "¥8.27"; panel (``precise``):
 *  "$1.2345". "Included in plan" for plans, null when unpriced. */
function costLabel(
  u: TaskUsage,
  t: (k: string) => string,
  currency: Currency,
  precise = false,
): string | null {
  if (u.cost_basis === 'catalog') {
    return formatCost(u.cost_usd ?? 0, currency, precise)
  }
  if (u.cost_basis === 'included') return t('usage.included')
  return null
}

/**
 * Context bar + cost for a task, with a click-open breakdown. Renders
 * nothing until the task has metered at least one API call.
 */
export function TaskUsageMeter({ usage }: { usage: TaskUsage }) {
  const { t, i18n } = useTranslation()
  const currency = currencyFor(i18n.language)
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [open])

  const tokens =
    (usage.input_tokens ?? 0) + (usage.output_tokens ?? 0) +
    (usage.cache_read_tokens ?? 0) + (usage.cache_write_tokens ?? 0)
  if (usage.context_tokens == null && tokens === 0) return null

  const pct = contextPercent(usage)
  const cost = costLabel(usage, t, currency)
  const preciseCost = costLabel(usage, t, currency, true)
  const barColor =
    pct == null ? 'bg-gray-300'
      : pct >= 90 ? 'bg-red-500'
        : pct >= 70 ? 'bg-amber-500'
          : 'bg-indigo-500'
  const note =
    usage.cost_basis === 'catalog'
      ? t('usage.estimateNote') +
        (currency === 'CNY' ? ' ' + t('usage.cnyRateNote', { rate: USD_TO_CNY }) : '')
      : usage.cost_basis === 'included' ? t('usage.includedNote')
        : t('usage.unpricedNote')

  const rows: [string, string][] = [
    [t('usage.model'), usage.usage_model || '—'],
    [
      t('usage.contextUsed'),
      usage.context_tokens == null ? '—'
        : usage.context_window
          ? `${formatTokens(usage.context_tokens)} / ${formatTokens(usage.context_window)} (${pct}%)`
          : formatTokens(usage.context_tokens),
    ],
    [t('usage.input'), formatTokens(usage.input_tokens ?? 0)],
    [t('usage.cacheRead'), formatTokens(usage.cache_read_tokens ?? 0)],
    [t('usage.cacheWrite'), formatTokens(usage.cache_write_tokens ?? 0)],
    [t('usage.output'), formatTokens(usage.output_tokens ?? 0)],
    [t('usage.turns'), String(usage.num_turns ?? 0)],
    [t('usage.cost'), preciseCost ?? t('usage.unpriced')],
  ]

  return (
    <div ref={ref} className="relative flex-shrink-0">
      <button
        type="button"
        data-testid="task-usage-meter"
        aria-expanded={open}
        onClick={() => setOpen(o => !o)}
        className="flex items-center gap-1.5 px-2 py-1 rounded-md text-xs text-gray-500 hover:bg-gray-100"
      >
        {pct != null && (
          <span className="w-12 h-1.5 rounded-full bg-gray-200 overflow-hidden" aria-hidden>
            <span className={`block h-full ${barColor}`} style={{ width: `${Math.max(pct, 4)}%` }} />
          </span>
        )}
        <span data-testid="task-usage-context">
          {pct != null
            ? `${pct}% · ${t('usage.contextOf', { used: formatTokens(usage.context_tokens ?? 0), window: formatTokens(usage.context_window ?? 0) })}`
            : `${t('usage.context')} ${formatTokens(usage.context_tokens ?? tokens)}`}
        </span>
        {cost && (
          <span data-testid="task-usage-cost" className="text-gray-700">· {cost}</span>
        )}
      </button>
      {open && (
        <div
          role="dialog"
          aria-label={t('usage.title')}
          data-testid="task-usage-details"
          className="absolute bottom-full mb-2 left-0 z-20 w-64 rounded-lg border border-gray-200 bg-white shadow-lg p-3 text-xs"
        >
          <div className="font-semibold text-gray-700 mb-2">{t('usage.title')}</div>
          <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
            {rows.map(([k, v]) => (
              <div key={k} className="contents">
                <dt className="text-gray-500">{k}</dt>
                <dd className="text-gray-800 text-right truncate" title={v}>{v}</dd>
              </div>
            ))}
          </dl>
          <p className="mt-2 text-[11px] text-gray-400 leading-snug">{note}</p>
        </div>
      )}
    </div>
  )
}
