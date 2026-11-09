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

export function formatCost(usd: number): string {
  if (usd > 0 && usd < 0.01) return '<$0.01'
  return `$${usd < 100 ? usd.toFixed(2) : Math.round(usd)}`
}

/** Context fill in percent, clamped; null without a known window. */
export function contextPercent(u: TaskUsage): number | null {
  if (!u.context_window || u.context_tokens == null) return null
  return Math.min(100, Math.round((u.context_tokens / u.context_window) * 100))
}
