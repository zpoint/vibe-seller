/** The context window Claude Code will actually use for a model under a
 *  profile's env, for the model chips' badge.
 *
 *  A model's documented window (`context` in the catalog, e.g. "1M") is
 *  not what Claude Code budgets with. Its own rule (src/utils/context.ts,
 *  CLI 2.1.3xx) for a third-party model, in order:
 *    1. `CLAUDE_CODE_MAX_CONTEXT_TOKENS` set -> that value;
 *    2. the id ends in `[1m]` -> 1,000,000;
 *    3. otherwise -> the 200K default.
 *  So `MiniMax-M3.1-Flash` and `MiniMax-M3.1-Flash[1m]` are the same model
 *  but a 200K and a 1M session. The badge shows the smaller of that window
 *  and the model's own, so it never promises more than either side allows.
 */
const DEFAULT_WINDOW = 200_000
const ONE_M = 1_000_000

export function parseWindow(text?: string | null): number | undefined {
  const m = text?.trim().match(/^(\d+(?:\.\d+)?)\s*([KkMm])?$/)
  if (!m) return undefined
  const n = parseFloat(m[1])
  const unit = m[2]?.toUpperCase()
  return Math.round(unit === 'M' ? n * ONE_M : unit === 'K' ? n * 1000 : n)
}

export function formatWindow(tokens: number): string {
  if (tokens >= ONE_M) {
    const m = Math.round((tokens / ONE_M) * 10) / 10
    return `${m}M`
  }
  return `${Math.round(tokens / 1000)}K`
}

export function claudeCodeWindow(
  modelId: string,
  maxContextTokens?: string,
): number {
  const override = parseInt(maxContextTokens ?? '', 10)
  if (!isNaN(override) && override > 0) return override
  if (/\[1m\]$/i.test(modelId.trim())) return ONE_M
  return DEFAULT_WINDOW
}

/** Badge text for a chip, or undefined when nothing is known. */
export function effectiveWindowLabel(
  modelId: string,
  nativeContext: string | undefined,
  maxContextTokens?: string,
): string | undefined {
  const cc = claudeCodeWindow(modelId, maxContextTokens)
  const native = parseWindow(nativeContext)
  return formatWindow(native ? Math.min(cc, native) : cc)
}
