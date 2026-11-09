import type { TaskUsage } from '../types'

/** 950 → "950", 45_300 → "45.3k", 452_300 → "452k", 1_048_576 → "1M". */
export function formatTokens(n: number): string {
  if (n < 1000) return String(n)
  const trim = (x: string) => x.replace(/\.0$/, '')
  if (n < 1_000_000) {
    const k = n / 1000
    return `${k < 100 ? trim(k.toFixed(1)) : Math.round(k)}k`
  }
  return `${trim((n / 1_000_000).toFixed(1))}M`
}

export type Currency = 'USD' | 'CNY'

/** USD → CNY for display. Prices are estimates from USD catalog rates,
 *  so a fixed rate is precise enough; the panel note states it. */
export const USD_TO_CNY = 6.7

/** Chinese UI shows RMB, everything else USD. */
export function currencyFor(language: string | undefined): Currency {
  return language?.toLowerCase().startsWith('zh') ? 'CNY' : 'USD'
}

/**
 * A USD figure in the display currency.
 * - default (the footer chip): two decimals, e.g. "$0.01" / "¥0.05";
 * - ``precise`` (the details panel): four decimals, e.g. "$0.0067".
 */
export function formatCost(
  usd: number,
  currency: Currency = 'USD',
  precise = false,
): string {
  const symbol = currency === 'CNY' ? '¥' : '$'
  const amount = currency === 'CNY' ? usd * USD_TO_CNY : usd
  return `${symbol}${amount.toFixed(precise ? 4 : 2)}`
}

/** Context fill in percent, clamped; null without a known window. */
export function contextPercent(u: TaskUsage): number | null {
  if (!u.context_window || u.context_tokens == null) return null
  return Math.min(100, Math.round((u.context_tokens / u.context_window) * 100))
}
