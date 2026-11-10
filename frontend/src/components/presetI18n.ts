import type { TFunction } from 'i18next'

/** Localise the server's provider-preset display strings.
 *
 *  The server keeps one canonical English copy of every preset (it is
 *  data shared by the API, MCP tools and telemetry). Display strings
 *  follow a small grammar — `Head` or `Head (term, term, ...)`, e.g.
 *  "Pay-as-you-go (China)", "Qwen3.8-Flash (fast, vision)" — so the UI
 *  translates the head and each term through `profiles.presetTerms`
 *  and re-joins them with the locale's own brackets and separator.
 *  Anything without a translation (model ids, brand and product names
 *  such as "Token Plan") passes through unchanged, so a new preset
 *  still renders before its terms are translated.
 */
const PAREN = /^(.*?)\s*\((.+)\)$/

export function localizePresetText(text: string, t: TFunction): string {
  const term = (s: string) =>
    t(`profiles.presetTerms.${s}`, { defaultValue: s }) as string
  const m = text.match(PAREN)
  if (!m) return term(text)
  const parts = m[2].split(/\s*,\s*/).map(term)
  return t('profiles.presetParen', {
    defaultValue: '{{head}} ({{parts}})',
    head: term(m[1]),
    parts: parts.join(
      t('profiles.presetSep', { defaultValue: ', ' }) as string,
    ),
  }) as string
}

interface LocalizablePreset {
  name: string
  group?: string
  variant?: string
  models?: { label: string }[]
}

/** Return the presets with every display string localised. `group`
 *  stays the stable grouping key; its display text goes in
 *  `groupLabel`. */
export function localizePresets<P extends LocalizablePreset>(
  presets: Record<string, P>,
  t: TFunction,
): Record<string, P & { groupLabel?: string }> {
  const out: Record<string, P & { groupLabel?: string }> = {}
  for (const [id, p] of Object.entries(presets)) {
    out[id] = {
      ...p,
      name: localizePresetText(p.name, t),
      variant: p.variant && localizePresetText(p.variant, t),
      groupLabel: p.group && localizePresetText(p.group, t),
      models: p.models?.map((m) => ({
        ...m,
        label: localizePresetText(m.label, t),
      })),
    }
  }
  return out
}
